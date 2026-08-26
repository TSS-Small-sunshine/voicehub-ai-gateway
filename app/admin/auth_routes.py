"""VoiceHub AI Gateway — 管理台路由（登录/登出/改密）。"""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request, Response

from ..auth import (
    authenticate,
    change_password,
    create_session,
    revoke_session,
    validate_session,
)
from ..db import GatewaySession, GwAuditLog
from .decorators import login_required
from .templates import render, render_request

router = APIRouter(prefix="/admin", tags=["admin-auth"])


def _set_session_cookie(resp: Response, token: str, max_age: int) -> None:
    resp.set_cookie(
        "gw_session",
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/admin",
    )


@router.get("/login")
async def login_form(request: Request):
    session = GatewaySession()
    try:
        result = validate_session(session, request.cookies.get("gw_session") or "")
    finally:
        session.close()
    if result:
        return Response(status_code=303, headers={"Location": "/admin/"})
    return render_request(request, "login.html", error=None, username="")


@router.post("/login")
async def login_submit(
    request: Request,
    response: Response,
    username: str = Form(""),
    password: str = Form(""),
):
    ip = request.client.host if request.client else None
    session = GatewaySession()
    try:
        user = authenticate(session, username, password, ip)
        if not user:
            session.add(GwAuditLog(actor=username or "(unknown)", action="login_failed", ip=ip))
            session.commit()
            return render_request(request, "login.html", error="账号或密码错误", username=username)
        token, csrf = create_session(session, user.id, ip, request.headers.get("user-agent", "")[:256])
        session.add(GwAuditLog(actor=user.username, action="login", ip=ip))
        session.commit()
    finally:
        session.close()

    target = "/admin/change-password" if user.must_change_password else "/admin/"
    resp = Response(status_code=303, headers={"Location": target})
    _set_session_cookie(resp, token, max_age=12 * 3600)
    return resp


@router.post("/logout")
@login_required
async def logout(request: Request, response: Response):
    token = request.cookies.get("gw_session")
    session = GatewaySession()
    try:
        revoke_session(session, token or "")
        user = request.state.user
        session.add(GwAuditLog(actor=user.username, action="logout", ip=request.client.host if request.client else None))
        session.commit()
    finally:
        session.close()
    resp = Response(status_code=303, headers={"Location": "/admin/login"})
    resp.delete_cookie("gw_session", path="/admin")
    return resp


@router.get("/change-password")
@login_required
async def change_password_form(request: Request):
    return render_request(request, "change_password.html", user=request.state.user, error=None, must=request.state.user.must_change_password)


@router.post("/change-password")
@login_required
async def change_password_submit(
    request: Request,
    old_password: str = Form(""),
    new_password: str = Form(""),
    confirm: str = Form(""),
):
    from ..auth import verify_password
    user = request.state.user
    if not verify_password(old_password, user.password_hash):
        return render_request(request, "change_password.html", user=user, error="旧密码错误", must=user.must_change_password)
    if len(new_password) < 8:
        return render_request(request, "change_password.html", user=user, error="新密码至少 8 位", must=user.must_change_password)
    if new_password != confirm:
        return render_request(request, "change_password.html", user=user, error="两次输入不一致", must=user.must_change_password)
    session = GatewaySession()
    try:
        change_password(session, user, new_password)
        session.add(GwAuditLog(actor=user.username, action="change_password", ip=request.client.host if request.client else None))
        session.commit()
    finally:
        session.close()
    return Response(status_code=303, headers={"Location": "/admin/"})