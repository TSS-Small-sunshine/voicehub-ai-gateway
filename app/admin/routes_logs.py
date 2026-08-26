"""VoiceHub AI Gateway — 管理台：审核日志检索（脱敏视图）。"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import desc

from ..db import AiReviewLog, ReviewSession
from ..mask import mask_pii
from .decorators import login_required
from .templates import render_request

router = APIRouter(prefix="/admin/logs", tags=["admin-logs"])


@router.get("")
@login_required
async def logs_page(
    request: Request,
    scene: str | None = None,
    decision: str | None = None,
    q: str | None = None,
    model_f: str | None = None,
    limit: int = 50,
):
    session = ReviewSession()
    try:
        query = session.query(AiReviewLog)
        if scene and scene != "all":
            query = query.filter(AiReviewLog.scene == scene)
        if decision and decision != "all":
            query = query.filter(AiReviewLog.decision == decision)
        if model_f:
            query = query.filter(AiReviewLog.model.contains(model_f.strip()[:64]))
        if q:
            from sqlalchemy import or_
            like = f"%{q.strip()[:100]}%"
            query = query.filter(or_(AiReviewLog.reason.like(like), AiReviewLog.payload_json.like(like)))
        rows = query.order_by(desc(AiReviewLog.id)).limit(min(max(limit, 1), 500)).all()
        # viewer 仅脱敏
        user = request.state.user
        show_raw = user.role == "admin"
        items = []
        for r in rows:
            pj = r.payload_json or ""
            if not show_raw and pj:
                try:
                    import json as _json
                    from ..mask import mask_field_name, mask_field_student_no
                    obj = _json.loads(pj)
                    masked = {}
                    for k, v in obj.items():
                        if isinstance(v, str):
                            s = mask_pii(v)
                            if k in ("name", "username", "学生姓名", "姓名"):
                                s = mask_field_name(s)
                            elif k in ("student_no", "studentId", "学号"):
                                s = mask_field_student_no(s)
                            masked[k] = s
                        else:
                            masked[k] = v
                    pj = _json.dumps(masked, ensure_ascii=False)
                except Exception:
                    pj = mask_pii(pj)
            items.append({
                "id": r.id,
                "scene": r.scene,
                "target_id": r.target_id,
                "decision": r.decision,
                "source": r.source or "",
                "model": r.model or "",
                "reason": (r.reason or "")[:200],
                "payload_json": pj[:400] + ("..." if len(pj) > 400 else ""),
                "duration_ms": r.duration_ms or 0,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            })
    finally:
        session.close()
    return render_request(
        request,
        "logs.html",
        user=user,
        items=items,
        scenes=["all", "register", "note", "song", "language", "replay_note"],
        decisions=["all", "APPROVE", "REJECT", "REVIEW"],
        scene_f=scene or "all",
        decision_f=decision or "all",
        model_f=model_f or "",
        q=q or "",
        show_raw=show_raw,
    )


@router.get("/export")
@login_required
async def logs_export(request: Request):
    session = ReviewSession()
    try:
        rows = session.query(AiReviewLog).order_by(desc(AiReviewLog.id)).limit(2000).all()
        is_admin = request.state.user.role == "admin"
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "scene", "target_id", "decision", "source", "model", "reason", "duration_ms", "created_at"])
        for r in rows:
            reason = (r.reason or "")[:500]
            if not is_admin:
                # viewer 导出同样脱敏，防旁路
                from ..mask import mask_pii
                reason = mask_pii(reason)
            w.writerow([r.id, r.scene, r.target_id, r.decision, r.source or "", r.model or "", reason, r.duration_ms or 0, r.created_at.isoformat() if r.created_at else ""])
        buf.seek(0)
    finally:
        session.close()
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=ai_review_logs.csv"})