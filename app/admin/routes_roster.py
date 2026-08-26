"""VoiceHub AI Gateway — 管理台：名册导入（CSV 预览确认）与查阅。"""
# 注意：本文件不可用 `from __future__ import annotations`（Python 3.14 + FastAPI
# 组合下 UploadFile 字符串前向引用解析失败）；其余 admin 路由无此参数不受影响。
import base64

from fastapi import APIRouter, File, Form, Request, Response, UploadFile

from ..roster import (
    MAX_CSV_BYTES,
    RosterImportError,
    import_rows,
    list_roster,
    parse_roster_csv,
    roster_size,
)
from .decorators import csrf_protect, login_required, require_role
from .templates import render_request

router = APIRouter(prefix="/admin/roster", tags=["admin-roster"])

_PREVIEW_SAMPLE = 20


def _audit(request: Request, action: str, target: str, before: str | None = None, after: str | None = None) -> None:
    from ..db import GatewaySession, GwAuditLog
    session = GatewaySession()
    try:
        session.add(GwAuditLog(
            actor=request.state.user.username,
            action=action,
            target=target,
            before_json=before,
            after_json=after,
            ip=request.client.host if request.client else None,
        ))
        session.commit()
    finally:
        session.close()


@router.get("")
@login_required
@require_role("admin")
async def roster_page(request: Request, msg: str = "", err: str = ""):
    entries = list_roster(limit=200)
    return render_request(
        request,
        "roster.html",
        user=request.state.user,
        entries=entries,
        total=roster_size(),
        msg=msg[:200],
        err=err[:200],
    )


@router.post("/preview")
@login_required
@require_role("admin")
async def roster_preview(request: Request, file: UploadFile = File(...)):
    await csrf_protect(request)
    try:
        data = await file.read()
        rows = parse_roster_csv(data)
    except RosterImportError as e:
        from urllib.parse import quote
        return Response(status_code=303, headers={"Location": f"/admin/roster?err={quote(str(e))}"})
    if len(data) > MAX_CSV_BYTES:
        from urllib.parse import quote
        return Response(status_code=303, headers={"Location": "/admin/roster?err=%E6%96%87%E4%BB%B6%E8%B6%85%E8%BF%875MB"})
    b64 = base64.b64encode(data).decode("ascii")
    return render_request(
        request,
        "roster.html",
        user=request.state.user,
        entries=list_roster(limit=200),
        total=roster_size(),
        msg="", err="",
        preview_rows=rows[:_PREVIEW_SAMPLE],
        preview_total=len(rows),
        data_b64=b64,
    )


@router.post("/import")
@login_required
@require_role("admin")
async def roster_import(request: Request, data_b64: str = Form("")):
    await csrf_protect(request)
    from urllib.parse import quote

    def back(msg_key: str, detail: str = "") -> Response:
        return Response(status_code=303, headers={"Location": f"/admin/roster?{msg_key}={quote(detail)}"})

    try:
        data = base64.b64decode(data_b64.encode("ascii"))
        rows = parse_roster_csv(data)
    except (ValueError, RosterImportError):
        return back("err", "导入数据无效或已过期，请重新上传")

    from ..db import GatewaySession
    session = GatewaySession()
    try:
        added, updated = import_rows(session, rows, actor=request.state.user.username)
    finally:
        session.close()
    _audit(request, "roster_import", f"rows={len(rows)}", after=f"added={added},updated={updated}")
    return back("msg", f"导入完成：新增 {added}，更新 {updated}，共 {len(rows)} 行")
