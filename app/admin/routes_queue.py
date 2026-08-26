"""VoiceHub AI Gateway — 管理台：待审队列 + 人工复核写回。

流程：
1. GET queue：按场景从主仓拉 pending（原始 payload 完整给 reviewer/admin）
2. POST action：调用主仓 /api/open/ai-review/result 写回 decision（reason 标注「人工」）
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Form, Request, Response

from ..db import GatewaySession, GwAuditLog
from ..mask import mask_pii
from ..voicehub_client import VoiceHubClient
from .decorators import csrf_protect, login_required, require_role
from .templates import render_request

log = logging.getLogger("ai-gateway")

router = APIRouter(prefix="/admin/queue", tags=["admin-queue"])

VALID_SCENES = ("register", "note", "song", "language")


async def _fetch_pending(scene: str, limit: int = 30) -> list[dict]:
    """实时拉取主仓 pending。SPEC [S5]：原文仅请求内窗口展示，不做跨请求缓存。"""
    client = VoiceHubClient()
    try:
        return await client.fetch_pending(scene, limit=limit)
    except Exception as e:
        log.warning("拉取待审失败：%s", e)
        return []
    finally:
        await client.close()


@router.get("")
@login_required
async def queue_page(request: Request, scene: str = "register"):
    if scene not in VALID_SCENES:
        scene = "register"
    items = await _fetch_pending(scene, limit=30)
    # viewer 仅看脱敏视图；reviewer/admin 看原文（队列场景原文需人工判断）
    user = request.state.user
    show_raw = user.role in ("reviewer", "admin")
    rendered = []
    for it in items:
        payload = it.get("payload") or {}
        if not show_raw:
            payload = _mask_payload(payload)
        rendered.append({
            "id": it.get("id"),
            "scene": it.get("scene") or scene,
            "payload": payload,
            "created_at": it.get("created_at", ""),
        })
    return render_request(
        request,
        "queue.html",
        user=user,
        scenes=VALID_SCENES,
        current_scene=scene,
        items=rendered,
    )


def _mask_payload(payload: dict) -> dict:
    return {k: mask_pii(str(v)) if isinstance(v, str) else v for k, v in payload.items()}


@router.post("/action")
@login_required
@require_role("reviewer", "admin")
async def review_action(
    request: Request,
    scene: str = Form(""),
    target_id: int = Form(0),
    decision: str = Form(""),
    reason: str = Form(""),
):
    await csrf_protect(request)
    if scene not in VALID_SCENES or target_id <= 0 or decision not in ("APPROVE", "REJECT", "REVIEW"):
        return Response(status_code=303, headers={"Location": f"/admin/queue?scene={scene or 'register'}"})

    full_reason = (reason or "").strip() or f"人工复核：{decision}"
    if "人工" not in full_reason:
        full_reason = f"人工复核：{full_reason}"

    client = VoiceHubClient()
    try:
        await client.submit_result(
            scene=scene,
            target_id=int(target_id),
            decision=decision,
            reason=full_reason[:500],
            confidence=1.0,
            model="human-reviewer",
            source="human",
            duration_ms=0,
        )
    except Exception as e:
        log.warning("人工写回失败：%s", e)
        return Response(status_code=303, headers={"Location": f"/admin/queue?scene={scene}&error=write"})
    finally:
        await client.close()

    session = GatewaySession()
    try:
        session.add(GwAuditLog(
            actor=request.state.user.username,
            action=f"review_{decision.lower()}",
            target=f"{scene}#{target_id}",
            before_json=None,
            after_json=json.dumps({"reason": full_reason}, ensure_ascii=False),
            ip=request.client.host if request.client else None,
        ))
        session.commit()
    finally:
        session.close()

    # 写回成功后下次进入队列页自然反映最新 pending（无跨请求缓存）
    return Response(status_code=303, headers={"Location": f"/admin/queue?scene={scene}"})