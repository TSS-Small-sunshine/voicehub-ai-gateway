"""VoiceHub AI Gateway — 供应商管理路由（admin only）。"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request, Response

from ..db import GatewaySession, GwAuditLog
from ..providers import (
    create_provider,
    decrypt_key,
    delete_provider,
    list_providers,
    seed_default_providers,
    update_provider,
)
from .auth_routes import csrf_protect  # noqa: F401（保留供后续模板继承）
from .decorators import login_required, require_role
from .templates import render_request

router = APIRouter(prefix="/admin/providers", tags=["admin-providers"])


@router.get("")
@login_required
@require_role("admin")
async def list_view(request: Request):
    session = GatewaySession()
    try:
        seed_default_providers(session)
        providers = list_providers(session)
        items = []
        for p in providers:
            d = decrypt_key(p.api_key_encrypted)
            items.append({
                "id": p.id,
                "name": p.name,
                "base_url": p.base_url,
                "model": p.model,
                "has_key": bool(d),
                "key_preview": (d[:4] + "****" + d[-2:]) if d else "",
                "enabled": p.enabled,
                "priority": p.priority,
                "timeout_seconds": p.timeout_seconds,
                "max_tokens": p.max_tokens,
                "is_builtin": p.is_builtin,
                "note": p.note or "",
            })
    finally:
        session.close()
    return render_request(request, "providers.html", user=request.state.user, providers=items, message=None)


@router.post("/create")
@login_required
@require_role("admin")
async def create_view(
    request: Request,
    name: str = Form(""),
    base_url: str = Form(""),
    model: str = Form(""),
    api_key: str = Form(""),
    priority: int = Form(100),
    timeout_seconds: float = Form(5.0),
    max_tokens: int = Form(512),
    note: str = Form(""),
):
    await csrf_protect(request)
    session = GatewaySession()
    try:
        create_provider(
            session,
            name=name.strip(),
            base_url=base_url.strip(),
            model=model.strip(),
            api_key=api_key.strip(),
            priority=priority,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
            note=note.strip(),
            enabled=bool(api_key.strip()),
        )
        session.add(GwAuditLog(actor=request.state.user.username, action="provider_create", target=name))
        session.commit()
    finally:
        session.close()
    return Response(status_code=303, headers={"Location": "/admin/providers"})


@router.post("/{provider_id}/update")
@login_required
@require_role("admin")
async def update_view(
    request: Request,
    provider_id: int,
    base_url: str = Form(""),
    model: str = Form(""),
    api_key: str = Form(""),
    priority: int = Form(100),
    timeout_seconds: float = Form(5.0),
    max_tokens: int = Form(512),
    enabled: str = Form(""),
    note: str = Form(""),
):
    await csrf_protect(request)
    fields = {
        "base_url": base_url.strip(),
        "model": model.strip(),
        "priority": priority,
        "timeout_seconds": timeout_seconds,
        "max_tokens": max_tokens,
        "enabled": enabled == "on",
        "note": note.strip(),
    }
    if api_key.strip():
        fields["api_key"] = api_key.strip()
    session = GatewaySession()
    try:
        update_provider(session, provider_id, **fields)
        session.add(GwAuditLog(actor=request.state.user.username, action="provider_update", target=str(provider_id)))
        session.commit()
    finally:
        session.close()
    return Response(status_code=303, headers={"Location": "/admin/providers"})


@router.post("/{provider_id}/delete")
@login_required
@require_role("admin")
async def delete_view(request: Request, provider_id: int):
    await csrf_protect(request)
    session = GatewaySession()
    try:
        ok = delete_provider(session, provider_id)
        if ok:
            session.add(GwAuditLog(actor=request.state.user.username, action="provider_delete", target=str(provider_id)))
            session.commit()
    finally:
        session.close()
    return Response(status_code=303, headers={"Location": "/admin/providers"})