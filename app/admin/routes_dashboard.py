"""VoiceHub AI Gateway — 看板（聚合统计）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import func

from ..db import AiReviewLog, GatewaySession, ReviewSession
from .decorators import login_required
from .templates import render_request

router = APIRouter(prefix="/admin", tags=["admin-dashboard"])


@router.get("/")
@router.get("")
@login_required
async def dashboard(request: Request):
    session = ReviewSession()
    try:
        now = datetime.utcnow()
        day_ago = now - timedelta(days=1)

        # 按场景与决策分桶（24h 内）
        rows = (
            session.query(AiReviewLog.scene, AiReviewLog.decision, func.count(AiReviewLog.id))
            .filter(AiReviewLog.created_at >= day_ago)
            .group_by(AiReviewLog.scene, AiReviewLog.decision)
            .all()
        )
        stats: dict[str, dict[str, int]] = {}
        for scene, decision, cnt in rows:
            stats.setdefault(scene, {})[decision] = cnt

        # LLM 调用量 / 成功率 / 延迟 p50
        llm_rows = (
            session.query(AiReviewLog.decision, AiReviewLog.duration_ms)
            .filter(AiReviewLog.created_at >= day_ago)
            .filter(AiReviewLog.source == "l2_llm")
            .all()
        )
        total_llm = len(llm_rows)
        ok_llm = sum(1 for d, _ in llm_rows if d != "REVIEW")
        success_rate = (ok_llm / total_llm * 100) if total_llm else 0.0
        durations = sorted(d for _, d in llm_rows if d is not None)
        p50 = durations[len(durations) // 2] if durations else 0

        def _p95(seq: list[int]) -> int:
            if not seq:
                return 0
            k = min(len(seq) - 1, int(round(0.95 * (len(seq) - 1))))
            return seq[k]

        p95 = _p95(durations)

        # 最近 10 条
        recent = (
            session.query(AiReviewLog)
            .order_by(AiReviewLog.created_at.desc())
            .limit(10)
            .all()
        )
        recent_items = [
            {
                "id": r.id,
                "scene": r.scene,
                "decision": r.decision,
                "source": r.source or "",
                "duration_ms": r.duration_ms or 0,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in recent
        ]
    finally:
        session.close()

    # 抽查摘要（SPEC [S8]：结果进看板；读失败不阻断看板）
    spot_total = spot_flagged = 0
    try:
        from ..db import GwSpotcheckLog
        gsession = GatewaySession()
        try:
            spot_total = gsession.query(func.count(GwSpotcheckLog.id)).scalar() or 0
            spot_flagged = (
                gsession.query(func.count(GwSpotcheckLog.id))
                .filter(GwSpotcheckLog.recheck_decision == "REJECT")
                .scalar() or 0
            )
        finally:
            gsession.close()
    except Exception:
        pass

    return render_request(
        request,
        "dashboard.html",
        user=request.state.user,
        stats=stats,
        total_llm=total_llm,
        success_rate=round(success_rate, 1),
        p50=p50,
        p95=p95,
        spot_total=spot_total,
        spot_flagged=spot_flagged,
        recent=recent_items,
    )