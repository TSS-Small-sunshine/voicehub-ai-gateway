"""VoiceHub AI Gateway — 管理台：定期抽查页（记录列表 + 手动触发）。"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response
from sqlalchemy import desc

from ..db import GwSpotcheckLog, ReviewSession
from ..settings import get_settings, parse_bool
from .decorators import csrf_protect, login_required, require_role
from .templates import render_request

router = APIRouter(prefix="/admin/spotcheck", tags=["admin-spotcheck"])


@router.get("")
@login_required
@require_role("reviewer", "admin")
async def spotcheck_page(request: Request, msg: str = "", err: str = ""):
    session = ReviewSession()
    try:
        logs = session.query(GwSpotcheckLog).order_by(desc(GwSpotcheckLog.id)).limit(100).all()
        items = [
            {
                "id": r.id,
                "scene": r.scene,
                "target_id": r.target_id,
                "original": r.original_decision,
                "recheck": r.recheck_decision,
                "needs_human": (r.recheck_decision or "").upper() == "REJECT",
                "confidence": f"{r.confidence:.2f}" if isinstance(r.confidence, float) else "—",
                "model": r.model or "",
                "reason": (r.reason or "")[:160],
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in logs
        ]
    finally:
        session.close()
    cfg = get_settings()
    return render_request(
        request,
        "spotcheck.html",
        user=request.state.user,
        items=items,
        enabled=parse_bool(str(cfg.get("spotcheck_enabled") or "")),
        interval_hours=cfg.get("spotcheck_interval_hours"),
        batch=cfg.get("spotcheck_batch_size"),
        msg=msg[:200],
        err=err[:200],
    )


@router.post("/run")
@login_required
@require_role("admin")
async def spotcheck_run(request: Request):
    """手动触发一轮小批量复核（上限 10 条，避免请求阻塞）。"""
    await csrf_protect(request)
    from urllib.parse import quote

    if not parse_bool(str(get_settings().get("spotcheck_enabled") or "")):
        return Response(status_code=303, headers={"Location": "/admin/spotcheck?err=" + quote("抽查未启用，请先在设置页开启")})
    from ..workers.spotcheck import run_spotcheck_once
    checked = await run_spotcheck_once(limit=10)
    return Response(status_code=303, headers={"Location": "/admin/spotcheck?msg=" + quote(f"本轮复核 {checked} 条")})
