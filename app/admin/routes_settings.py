"""VoiceHub AI Gateway — 管理台：运行期设置页（保存即热生效）。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response

from ..settings import DEFAULTS, GROUPS, get_settings, set_setting, setting_sources
from .decorators import csrf_protect, login_required, require_role
from .templates import render_request

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"])

# 布尔键：复选框不勾=不在表单中；显式写 true/false，不做「空=清除覆盖」
_BOOL_KEYS = {"l3_enabled", "spotcheck_enabled", "register_channel_frozen"}


@router.get("")
@login_required
@require_role("admin")
async def settings_page(request: Request):
    return render_request(
        request,
        "settings.html",
        user=request.state.user,
        groups=GROUPS,
        values=get_settings(),
        sources=setting_sources(),
        saved=request.query_params.get("saved") == "1",
        err=(request.query_params.get("err") or "")[:400],
    )


@router.post("/save")
@login_required
@require_role("admin")
async def settings_save(request: Request):
    await csrf_protect(request)
    form = await request.form()
    before = get_settings()

    changes: dict[str, tuple[str, str]] = {}
    invalid: list[str] = []
    for key in DEFAULTS:
        if key in _BOOL_KEYS:
            raw = "true" if str(form.get(key, "")).strip() else "false"
        elif key not in form:
            continue
        else:
            raw = str(form.get(key) or "").strip()
            if "\n" in raw or len(raw) > 200:
                continue  # 超长/多行输入直接忽略
        if raw != before[key]:
            try:
                set_setting(key, raw)
            except ValueError as e:
                invalid.append(f"{key}: {e}")
                continue
            changes[key] = (before[key], raw)

    # 审计留痕
    if changes:
        from ..db import GatewaySession, GwAuditLog
        session = GatewaySession()
        try:
            session.add(GwAuditLog(
                actor=request.state.user.username,
                action="settings_save",
                target=",".join(changes.keys()),
                before_json=json.dumps({k: v[0] for k, v in changes.items()}, ensure_ascii=False),
                after_json=json.dumps({k: v[1] for k, v in changes.items()}, ensure_ascii=False),
                ip=request.client.host if request.client else None,
            ))
            session.commit()
        finally:
            session.close()

    from urllib.parse import quote
    if invalid:
        return Response(status_code=303, headers={"Location": "/admin/settings?err=" + quote("；".join(invalid)[:300])})
    return Response(status_code=303, headers={"Location": "/admin/settings?saved=1"})
