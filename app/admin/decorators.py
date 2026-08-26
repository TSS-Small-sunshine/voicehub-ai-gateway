"""VoiceHub AI Gateway — 管理台装饰器（登录/角色/CSRF）。"""
from __future__ import annotations

from functools import wraps
from typing import Iterable

from fastapi import HTTPException, Request

from ..auth import validate_session
from ..db import GatewaySession


def login_required(fn):
    @wraps(fn)
    async def wrapper(request: Request, *args, **kwargs):
        token = request.cookies.get("gw_session")
        session = GatewaySession()
        try:
            result = validate_session(session, token or "")
        finally:
            session.close()
        if not result:
            raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
        user, gw_session = result
        request.state.user = user
        request.state.gw_session = gw_session
        return await fn(request, *args, **kwargs)
    return wrapper


def require_role(*roles: str):
    """允许角色列表；admin 始终通行。"""
    allowed = set(roles)

    def deco(fn):
        @wraps(fn)
        async def wrapper(request: Request, *args, **kwargs):
            user = getattr(request.state, "user", None)
            if not user:
                raise HTTPException(status_code=401, detail="未登录")
            if user.role == "admin" or user.role in allowed:
                return await fn(request, *args, **kwargs)
            raise HTTPException(status_code=403, detail="无权限")
        return wrapper
    return deco


async def csrf_protect(request: Request) -> None:
    """依赖：校验表单 CSRF。"""
    gw_session = getattr(request.state, "gw_session", None)
    if not gw_session:
        raise HTTPException(status_code=401, detail="未登录")
    form = await request.form()
    submitted = form.get("_csrf", "")
    if not submitted:
        raise HTTPException(status_code=400, detail="缺少 CSRF 令牌")
    from ..auth import csrf_check
    if not csrf_check(gw_session.csrf_token, submitted):
        raise HTTPException(status_code=400, detail="CSRF 令牌无效或已过期")