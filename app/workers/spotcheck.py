"""VoiceHub AI Gateway — 定期抽查 worker（已判定通过记录复审，SPEC [S8]）。

- 从「已判定通过」的本地日志（register/note，APPROVE）按批抽样重新送 L2 复审；
- 结果写 gw_spotcheck_logs；原 APPROVE 而复审 REJECT → 页面标「待人工复核」；
- 绝不自动写回主仓/删号——安全第一，人工确认后才动作。
"""
from __future__ import annotations

import json
import logging

from ..reviewers import L2LlmReviewer
from ..settings import get_settings, parse_bool
from ..workers.poll_pending import build_review_text, build_system_prompt

log = logging.getLogger("ai-gateway")


async def run_spotcheck_once(l2: L2LlmReviewer | None = None, limit: int | None = None) -> int:
    """执行一次抽样复审，返回复核条数。LLM 实例可注入（测试）。"""
    cfg = get_settings()
    if not parse_bool(str(cfg.get("spotcheck_enabled") or "false")):
        return 0
    l2 = l2 or L2LlmReviewer()
    batch = limit or int(float(cfg.get("spotcheck_batch_size") or 20))

    from datetime import datetime
    from random import sample as _sample
    from sqlalchemy import func

    from ..db import AiReviewLog, GwSpotcheckLog, ReviewSession
    session = ReviewSession()
    checked = 0
    try:
        candidates = (
            session.query(AiReviewLog)
            .filter(AiReviewLog.scene.in_(("register", "note")))
            .filter(AiReviewLog.decision == "APPROVE")
            .order_by(func.random())
            .limit(max(batch * 3, 30))
            .all()
        )
        # 同一 target 只取一条，再截到批量
        seen: set[tuple[str, int]] = set()
        picked: list[AiReviewLog] = []
        for r in candidates:
            key = (r.scene, r.target_id)
            if key in seen:
                continue
            seen.add(key)
            picked.append(r)
            if len(picked) >= batch:
                break

        for row in picked:
            try:
                payload = json.loads(row.payload_json or "{}")
            except Exception:
                payload = {}
            text = build_review_text(row.scene, payload)
            if not text.strip():
                continue
            try:
                result = await l2.review(build_system_prompt(row.scene), text)
            except Exception as e:
                log.warning("抽查复审 %s#%s 失败跳过: %s", row.scene, row.target_id, e)
                continue
            conf = result.get("confidence")
            try:
                conf = float(conf) if conf is not None else None
            except (TypeError, ValueError):
                conf = None
            session.add(GwSpotcheckLog(
                scene=row.scene,
                target_id=row.target_id,
                original_decision=row.decision,
                recheck_decision=result.get("decision") or "REVIEW",
                confidence=conf,
                model=result.get("source"),
                reason=(result.get("reason") or "")[:500],
                reviewed_by="system-spotcheck",
                created_at=datetime.utcnow(),
            ))
            checked += 1
        session.commit()
    finally:
        session.close()
    return checked


async def run_spotcheck_loop() -> None:
    """低频循环：每 interval 小时一轮（lifespan 启动；未启用时空转睡眠）。"""
    import asyncio

    from ..config import settings

    log.info("定期抽查任务已挂载（受 spotcheck_enabled 控制）")
    while True:
        try:
            await run_spotcheck_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("抽查轮异常: %s", e)
        try:
            interval = float(get_settings().get("spotcheck_interval_hours") or 24)
        except ValueError:
            interval = 24.0
        await asyncio.sleep(max(interval, 0.25) * 3600)
