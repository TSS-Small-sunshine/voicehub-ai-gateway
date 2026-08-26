"""VoiceHub AI Gateway — 管理台：L1 规则 CRUD（含 skip_scenes）+ 热生效。

规则唯一载体：reviewers/l1_rules 进程级单例；CRUD 落 gw_rules 后 reload，
worker 与本页面共享同一实例（热生效全链路贯通）。页面仅 admin 可见
（SPEC [S12] 反作弊：评审细节不外泄）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request, Response
from markupsafe import escape

from ..db import GatewaySession, GwAuditLog, GwRule
from ..reviewers.l1_rules import DEFAULT_PATTERNS, get_runtime, reload_runtime
from .decorators import csrf_protect, login_required, require_role
from .templates import render_request

router = APIRouter(prefix="/admin/rules", tags=["admin-rules"])


@router.get("")
@login_required
@require_role("admin")
async def list_view(request: Request):
    rules = get_runtime()._patterns
    builtin = [{"name": n, "pattern": p.pattern, "label": lb, "skip_scenes": sorted(s)} for (n, p, lb, s) in rules]
    session = GatewaySession()
    try:
        db_rules = session.query(GwRule).order_by(GwRule.id.desc()).limit(200).all()
    finally:
        session.close()
    return render_request(request, "rules.html", user=request.state.user, builtin=builtin, db_rules=db_rules,
                          err=(request.query_params.get("err") or "")[:300])


@router.post("/create")
@login_required
@require_role("admin")
async def create_rule(
    request: Request,
    name: str = Form(""),
    pattern: str = Form(""),
    label: str = Form(""),
    skip_scenes: str = Form(""),
):
    await csrf_protect(request)
    from urllib.parse import quote
    name = name.strip()[:64]
    pattern = pattern.strip()[:512]
    label = label.strip()[:64]
    if not name or not pattern or not label:
        return Response(status_code=303, headers={"Location": "/admin/rules?err=" + quote("名称/正则/标签均必填")})
    try:
        __import__("re").compile(pattern)
    except Exception as e:
        return Response(status_code=303, headers={"Location": "/admin/rules?err=" + quote(f"正则错误：{e}")[:200]})
    scenes_json = json.dumps([s.strip() for s in skip_scenes.split(",") if s.strip()], ensure_ascii=False)

    from sqlalchemy.exc import IntegrityError
    session = GatewaySession()
    try:
        session.add(GwRule(name=name, pattern=pattern, label=label, skip_scenes_json=scenes_json, enabled=True))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return Response(status_code=303, headers={"Location": "/admin/rules?err=" + quote("规则名已存在")})
        _audit(request, "rule_create", name, after=json.dumps({"pattern": pattern, "skip_scenes_json": scenes_json}, ensure_ascii=False), session=session)
    finally:
        session.close()
    reload_runtime()  # 保存即热生效
    return Response(status_code=303, headers={"Location": "/admin/rules"})


@router.post("/update")
@login_required
@require_role("admin")
async def update_rule(
    request: Request,
    rule_id: int = Form(0),
    enabled: str = Form(""),
    label: str = Form(""),
    skip_scenes: str = Form(""),
):
    """启停 / 改标签与跳过场景（正则本体改错风险高，走删除重建）。"""
    await csrf_protect(request)
    from urllib.parse import quote
    if rule_id <= 0:
        return Response(status_code=303, headers={"Location": "/admin/rules?err=" + quote("无效 ID")})
    session = GatewaySession()
    try:
        r = session.query(GwRule).filter(GwRule.id == rule_id).one_or_none()
        if not r:
            return Response(status_code=303, headers={"Location": "/admin/rules?err=" + quote("规则不存在")})
        before = {"enabled": r.enabled, "label": r.label}
        r.enabled = bool(enabled)
        if label.strip():
            r.label = label.strip()[:64]
        if skip_scenes:
            r.skip_scenes_json = json.dumps([s.strip() for s in skip_scenes.split(",") if s.strip()], ensure_ascii=False)
        session.commit()
        _audit(request, "rule_update", r.name,
               before=json.dumps(before), after=json.dumps({"enabled": r.enabled, "label": r.label}), session=session)
    finally:
        session.close()
    reload_runtime()
    return Response(status_code=303, headers={"Location": "/admin/rules"})


@router.post("/delete")
@login_required
@require_role("admin")
async def delete_rule(request: Request, rule_id: int = Form(0)):
    await csrf_protect(request)
    session = GatewaySession()
    try:
        r = session.query(GwRule).filter(GwRule.id == rule_id).one_or_none()
        if r:
            name = r.name
            session.delete(r)
            session.commit()
            _audit(request, "rule_delete", name, before=json.dumps({"id": rule_id}), session=session)
    finally:
        session.close()
    reload_runtime()
    return Response(status_code=303, headers={"Location": "/admin/rules"})


def _audit(request: Request, action: str, target: str, before: str | None = None, after: str | None = None, session=None) -> None:
    """写管理审计；可用调用方活动会话（内部完成 commit）或自开临时会话。"""
    owned = False
    if session is None:
        session = GatewaySession()
        owned = True
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
        if owned:
            session.close()


@router.post("/reload")
@login_required
@require_role("admin")
async def reload(request: Request):
    await csrf_protect(request)
    reload_runtime()
    _audit(request, "rules_reload", "global")
    return Response(status_code=303, headers={"Location": "/admin/rules"})


@router.post("/test")
@login_required
@require_role("admin")
async def test_pattern(request: Request, text: str = Form(""), pattern: str = Form(""), skip_scenes: str = Form("")):
    await csrf_protect(request)
    import re as _re
    try:
        compiled = _re.compile(pattern)
    except Exception as e:
        # 回显内容全部转义，杜绝反射点
        return Response(f"<div class='flash error'>正则错误：{escape(str(e))}</div>", media_type="text/html")
    skip = set(s.strip() for s in skip_scenes.split(",") if s.strip())
    matched = compiled.search(text)
    if matched:
        shown = escape(skip_scenes) if skip_scenes else "无跳过场景"
        return Response(f"<div class='flash'>命中（{shown}）</div>", media_type="text/html")
    return Response("<div class='muted'>未命中</div>", media_type="text/html")


@router.post("/seed-builtin")
@login_required
@require_role("admin")
async def seed_builtin(request: Request):
    """把内置规则同步进 GwRule 表（便于后台编辑/审计；不改默认行为）。"""
    await csrf_protect(request)
    session = GatewaySession()
    try:
        existing = {r.name for r in session.query(GwRule).all()}
        created = 0
        for item in DEFAULT_PATTERNS:
            if item["name"] in existing:
                continue
            session.add(GwRule(
                name=item["name"],
                pattern=item["pattern"],
                label=item["label"],
                skip_scenes_json=json.dumps(item.get("skip_scenes") or [], ensure_ascii=False),
                enabled=False,  # 默认禁用，需 admin 显式启用
                note="内置模板（未启用）",
            ))
            created += 1
        session.commit()
        _audit(request, "rules_seed_builtin", f"created={created}", session=session)
    finally:
        session.close()
    return Response(status_code=303, headers={"Location": "/admin/rules"})