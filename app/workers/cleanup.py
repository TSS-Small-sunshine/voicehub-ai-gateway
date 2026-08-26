"""VoiceHub AI Gateway — 日志保留期清理 worker（SPEC [S13] 留存与删除）。

按 log_retention_days（DB>env>默认 180 天）删除超期 AiReviewLog；管理操作审计
与抽查记录不在本任务范围。低频循环（每 6h 一轮），删除分批防长事务。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..settings import get_settings

log = logging.getLogger("ai-gateway")

_BATCH = 500


def cleanup_once(retention_days: int | None = None) -> int:
    """执行一次清理，返回删除行数。"""
    cfg = get_settings()
    if retention_days is None:
        try:
            retention_days = int(float(cfg.get("log_retention_days") or 180))
        except ValueError:
            retention_days = 180
    cutoff = datetime.utcnow() - timedelta(days=retention_days)

    from sqlalchemy import bindparam

    from ..db import AiReviewLog, ReviewSession
    session = ReviewSession()
    deleted = 0
    try:
        while True:
            ids = [
                row[0]
                for row in session.query(AiReviewLog.id)
                .filter(AiReviewLog.created_at < cutoff)
                .limit(_BATCH)
                .all()
            ]
            if not ids:
                break
            session.query(AiReviewLog).filter(AiReviewLog.id.in_(ids)).delete(synchronize_session=False)
            session.commit()
            deleted += len(ids)
    finally:
        session.close()
    if deleted:
        log.info("日志清理：删除 %d 条早于 %s 的记录", deleted, cutoff.date())
    return deleted


async def run_cleanup_loop() -> None:
    """每日一轮的保留期清理循环（lifespan 启动）。"""
    import asyncio

    log.info("日志保留期清理任务已挂载")
    while True:
        try:
            cleanup_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("清理轮异常: %s", e)
        await asyncio.sleep(6 * 3600)
