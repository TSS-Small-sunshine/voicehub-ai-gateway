"""VoiceHub AI Gateway — 管理台：L1 规则 CRUD（含 skip_scenes）+ 热生效。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Form, Request, Response

from ..db import GatewaySession, GwAuditLog, GwRule
from ..reviewers.l1_rules import DEFAULT_PATTERNS, L1RulesReviewer
from .decorators import login_required, require_role
from .templates import render_request

router = APIRouter(prefix="/admin/rules", tags=["admin-rules"])

# 模块级 L1 实例引用（worker 使用同一个），通过 update_hot_rules 热替换 _patterns
_runtime: L1RulesReviewer | None = None


def get_runtime() -> L1RulesReviewer:
    global _runtime
    if _runtime is None:
        _runtime = L1RulesReviewer()
    return _runtime


def reload_runtime() -> L1RulesReviewer:
    """从 DB + 内置默认重载 L1 规则集。"""
    global _runtime
    _runtime = L1RulesReviewer()
    session = GatewaySession()
    try:
        for r in session.query(GwRule).filter(GwRule.enabled.is_(True)).all():
            try:
                skip = set(json.loads(r.skip_scenes_json or "[]"))
            except Exception:
                skip = set()
            import re as _re
            _runtime._patterns.append((r.name, _re.compile(r.pattern), r.label, skip))
    finally:
        session.close()
    return _runtime


@router.get("")
@login_required
async def list_view(request: Request):
    rules = get_runtime()._patterns
    builtin = [{"name": n, "pattern": p.pattern, "label": lb, "skip_scenes": sorted(s)} for (n, p, lb, s) in rules]
    return render_request(request, "rules.html", user=request.state.user, builtin=builtin)


@router.post("/test")
@login_required
async def test_pattern(request: Request, text: str = Form(""), pattern: str = Form(""), skip_scenes: str = Form("")):
    import re as _re
    try:
        compiled = _re.compile(pattern)
    except Exception as e:
        return Response(f"<div class='flash error'>正则错误：{e}</div>", media_type="text/html")
    skip = set(s.strip() for s in skip_scenes.split(",") if s.strip())
    matched = compiled.search(text)
    if matched:
        return Response(f"<div class='flash'>命中（{skip_scenes or '无跳过场景'}）</div>", media_type="text/html")
    return Response("<div class='muted'>未命中</div>", media_type="text/html")


@router.post("/reload")
@login_required
@require_role("admin")
async def reload(request: Request):
    await _csrf(request)
    reload_runtime()
    session = GatewaySession()
    try:
        session.add(GwAuditLog(actor=request.state.user.username, action="rules_reload", target="global"))
        session.commit()
    finally:
        session.close()
    return Response(status_code=303, headers={"Location": "/admin/rules"})


async def _csrf(request: Request) -> None:
    from .decorators import csrf_protect
    await csrf_protect(request)


@router.post("/seed-builtin")
@login_required
@require_role("admin")
async def seed_builtin(request: Request):
    """把内置规则同步进 GwRule 表（便于后台编辑/审计；不改默认行为）。"""
    await _csrf(request)
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
        session.add(GwAuditLog(actor=request.state.user.username, action="rules_seed_builtin", target=f"created={created}"))
        session.commit()
    finally:
        session.close()
    return Response(status_code=303, headers={"Location": "/admin/rules"})