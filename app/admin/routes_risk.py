"""VoiceHub AI Gateway — 管理台：注册风控视图（SPEC [S12] 反作弊第三层）。

- 被拒/REVIEW 占比统计（近 500 条 register 日志）；
- 相似备注模板聚类（归一化数字/符号后分组，出现 ≥3 次视为可疑模板）；
- 通道冻结开关：写 register_channel_frozen，轮询下一轮跳过 register 场景。
- 同 IP/UA 聚合需主仓数据（网关日志不含 IP），一期标注「二期」。
"""
from __future__ import annotations

import json
import re
from collections import Counter

from fastapi import APIRouter, Request, Response
from sqlalchemy import desc, func

from ..db import AiReviewLog, ReviewSession
from ..settings import get_settings, parse_bool, set_setting
from .decorators import csrf_protect, login_required, require_role
from .templates import render_request

router = APIRouter(prefix="/admin/risk", tags=["admin-risk"])

_WINDOW = 500
_CLUSTER_MIN = 3
# 归一化：数字/字母/空白/符号全部折叠 —— 聚类捕捉「同模板不同填充」的备注
_NORM_RE = re.compile(r"[\da-zA-Z\s\W_]+", re.UNICODE)


def _cluster_remarks(payloads: list[dict]) -> list[dict]:
    counter: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for p in payloads:
        remark = str(p.get("remark") or "").strip()
        if len(remark) < 4:
            continue
        key = _NORM_RE.sub("", remark)[:40]
        if not key:
            continue
        counter[key] += 1
        samples.setdefault(key, remark[:60])
    out = []
    for key, count in counter.most_common(10):
        if count < _CLUSTER_MIN:
            break
        out.append({"key": key, "count": count, "sample": samples[key]})
    return out


def risk_snapshot(limit: int = _WINDOW) -> dict:
    """聚合近 limit 条注册审核日志的风控信号（供页面与测试复用）。"""
    session = ReviewSession()
    try:
        base = session.query(AiReviewLog).filter(AiReviewLog.scene == "register")
        total_all = base.count()
        rows = base.order_by(desc(AiReviewLog.id)).limit(limit).all()
        decisions = [r.decision for r in rows]
        n_approve = sum(1 for d in decisions if d == "APPROVE")
        n_review = sum(1 for d in decisions if d == "REVIEW")
        n_reject = sum(1 for d in decisions if d == "REJECT")
        payloads = []
        for r in rows:
            try:
                obj = json.loads(r.payload_json or "{}")
                if isinstance(obj, dict):
                    payloads.append(obj)
            except Exception:
                continue
        clusters = _cluster_remarks(payloads)
    finally:
        session.close()
    n = max(len(decisions), 1)

    def pct(v: int) -> str:
        return f"{v * 100 / n:.1f}%"

    return {
        "total_window": len(decisions),
        "total_all": total_all,
        "approve": n_approve,
        "approve_pct": pct(n_approve),
        "review": n_review,
        "review_pct": pct(n_review),
        "reject": n_reject,
        "reject_pct": pct(n_reject),
        "clusters": clusters,
    }


@router.get("")
@login_required
@require_role("admin")
async def risk_page(request: Request):
    snap = risk_snapshot()
    cfg = get_settings()
    return render_request(
        request,
        "risk.html",
        user=request.state.user,
        **snap,
        frozen=parse_bool(str(cfg.get("register_channel_frozen") or "")),
    )


@router.post("/freeze")
@login_required
@require_role("admin")
async def freeze_toggle(request: Request):
    await csrf_protect(request)
    form = await request.form()
    new_val = "true" if str(form.get("freeze", "")).strip() else "false"
    old = get_settings().get("register_channel_frozen") or "false"
    set_setting("register_channel_frozen", new_val)

    from ..db import GatewaySession, GwAuditLog
    session = GatewaySession()
    try:
        session.add(GwAuditLog(
            actor=request.state.user.username,
            action="register_channel_freeze",
            target=new_val,
            before_json=json.dumps({"register_channel_frozen": old}),
            after_json=json.dumps({"register_channel_frozen": new_val}),
            ip=request.client.host if request.client else None,
        ))
        session.commit()
    finally:
        session.close()
    return Response(status_code=303, headers={"Location": "/admin/risk"})
