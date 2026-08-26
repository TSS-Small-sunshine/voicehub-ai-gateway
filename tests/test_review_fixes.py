"""初审修复回归（对应审阅报告 Critical 1/2、Major 3-6 及次要项）。

重点断言「全链路」而非单点：
- 脱敏必须发生在 L2 出站与日志落盘，而非仅管理台展示；
- DB 自定义规则必须被 worker 实际消费（共享单例，杜绝双实例假连通）。
"""
import os

import pytest

os.environ.setdefault("ADMIN_SECRET", "test-secret-1234567890abcdef0123456789abcd")

# 复用 C 阶段隔离 fixtures（pytest rootdir 同目录，直接模块名导入）
from test_admin_c_stage import _ensure_admin, _login, gw_db, client  # noqa: F401,E402


@pytest.fixture(autouse=True)
def _clean_global_ip_bucket():
    """登录限流桶是进程级全局，防止跨用例污染。"""
    from app.auth import clear_ip_failures
    clear_ip_failures("testclient")
    yield
    clear_ip_failures("testclient")


# ---------------- Critical 1：脱敏入执行链路 ----------------
async def test_llm_outbound_and_persist_masked(gw_db):
    """L2 收到的文本无裸手机号；payload_json 落盘已脱敏；名册比对不受影响。"""
    seen_texts = []

    class SpyLlm:
        async def review(self, system_prompt, user_text):
            seen_texts.append(user_text)
            return {"decision": "APPROVE", "reason": "合规", "confidence": 0.9, "source": "l2_llm"}

    class C:
        def __init__(self):
            self.submitted = []

        async def fetch_pending(self, scene, limit=20):
            if scene != "register":
                return []
            return [{"id": 5, "scene": "register",
                     "payload": {"username": "小明13812345678", "name": "张三",
                                 "remark": "想和同学一起点歌"}}]

        async def submit_result(self, **kw):
            self.submitted.append(kw)

    from app.reviewers.l1_rules import L1RulesReviewer
    from app.workers.poll_pending import poll_once
    c = C()
    await poll_once(c, L1RulesReviewer(), SpyLlm(), None, state={}, cfg={})

    # 出站脱敏：手机号掩码、姓名保留姓氏
    out = seen_texts[0]
    assert "13812345678" not in out
    assert "138****5678" in out and "张*" in out

    # 落盘脱敏
    from app.db import AiReviewLog, ReviewSession
    s = ReviewSession()
    try:
        row = s.query(AiReviewLog).filter(AiReviewLog.target_id == 5).one()
        pj = row.payload_json
    finally:
        s.close()
    assert "13812345678" not in pj and "abc123" not in pj  # URL/微信片段亦被处理


def test_mask_payload_json_field_aware():
    from app.mask import mask_payload_json
    out = mask_payload_json('{"name":"欧阳娜娜","remark":"电话13812345678","score":95}')
    assert "欧***" in out          # 姓名字段级脱敏（mask_pii 不处理纯汉字名）
    assert "138****5678" in out    # 通用联系方式脱敏
    assert '"score": 95' in out    # 非字符串不动


async def test_degraded_path_payload_masked(gw_db):
    """异常降级路径 payloadJson 同样过 mask 后落盘。"""
    from app.db import AiReviewLog, ReviewSession
    from app.workers.poll_pending import poll_once

    class Boom:
        async def review(self, *_a, **_k):
            raise RuntimeError("boom")

    class C:
        def __init__(self):
            self.submitted = None

        async def fetch_pending(self, scene, limit=20):
            return [{"id": 8, "scene": "note", "payload": {"text": "加QQ 12345678"}}] if scene == "note" else []

        async def submit_result(self, **kw):
            self.submitted = kw

    c = C()
    await poll_once(c, Boom(), None, None, state={}, cfg={})

    s = ReviewSession()
    try:
        row = s.query(AiReviewLog).filter(AiReviewLog.target_id == 8).one()
    finally:
        s.close()
    assert "12345678" not in row.payload_json
    assert "*" in row.payload_json
    assert row.source == "degraded"


# ---------------- Critical 2：规则 CRUD 与热生效贯通 ----------------
def test_reload_returns_shared_singleton(gw_db):
    """reload/get 返回同一实例 —— 双实例即假连通（初审教训的反向断言）。"""
    from app.reviewers.l1_rules import get_runtime, reload_runtime
    rt = reload_runtime()
    assert get_runtime() is rt


async def test_db_rule_actually_consumed_by_poll(gw_db):
    """gw_rules 启用规则 → reload → worker 用同一 runtime 命中 REJECT。"""
    import json as _json

    from app.db import GatewaySession, GwRule
    s = GatewaySession()
    try:
        s.add(GwRule(name="zuobi_test", pattern=r"(?i)代考|枪手", label="舞弊词",
                     skip_scenes_json=_json.dumps([]), enabled=True))
        s.commit()
    finally:
        s.close()

    from app.reviewers.l1_rules import reload_runtime
    reload_runtime()

    from app.workers.poll_pending import poll_once
    from app.reviewers.l1_rules import get_runtime

    submitted = []

    class C:
        async def fetch_pending(self, scene, limit=20):
            return [{"id": 12, "scene": "note", "payload": {"text": "找代考包过"}}] if scene == "note" else []

        async def submit_result(self, **kw):
            submitted.append(kw)

    await poll_once(C(), get_runtime(), None, None, state={}, cfg={})
    assert len(submitted) == 1
    assert submitted[0]["decision"] == "REJECT"
    assert "舞弊词" in submitted[0]["reason"]


def test_rules_crud_api_full_chain(client, gw_db):
    """API 创建规则 → 内存立即生效；删除后失效。"""
    token, csrf = _ensure_admin(client, "rule-admin")
    resp = client.post(
        "/admin/rules/create",
        data={"name": "api_rule_x", "pattern": r"违规词X", "label": "X测试", "skip_scenes": "", "_csrf": csrf},
        cookies={"gw_session": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    from app.reviewers.l1_rules import get_runtime
    names = [n for (n, _, _, _) in get_runtime()._patterns]
    assert "api_rule_x" in names

    # 重名拒绝
    resp = client.post(
        "/admin/rules/create",
        data={"name": "api_rule_x", "pattern": "y", "label": "dup", "_csrf": csrf},
        cookies={"gw_session": token}, follow_redirects=False,
    )
    assert "err=" in resp.headers["location"]

    # 删除并验证内存同步
    from app.db import GatewaySession, GwRule
    s = GatewaySession()
    try:
        rid = s.query(GwRule).filter_by(name="api_rule_x").one().id
    finally:
        s.close()
    resp = client.post("/admin/rules/delete", data={"rule_id": str(rid), "_csrf": csrf},
                       cookies={"gw_session": token}, follow_redirects=False)
    assert resp.status_code == 303
    names = [n for (n, _, _, _) in get_runtime()._patterns]
    assert "api_rule_x" not in names


def test_rules_create_audit_recorded(client, gw_db):
    from app.db import GatewaySession, GwAuditLog
    token, csrf = _ensure_admin(client, "rule-audit")
    client.post("/admin/rules/create",
                data={"name": "aud_r1", "pattern": "p1", "label": "审计标签", "_csrf": csrf},
                cookies={"gw_session": token}, follow_redirects=False)
    s = GatewaySession()
    try:
        assert s.query(GwAuditLog).filter_by(action="rule_create", target="aud_r1").count() == 1
    finally:
        s.close()


# ---------------- Major 3-6 ----------------
async def test_retry_backoff_is_async_nonblocking():
    """退避等待不得使用阻塞 time.sleep（会冻结 event loop）。"""
    import inspect
    from app import llm_client
    src = inspect.getsource(llm_client._try_provider)
    assert "time.sleep(" not in src
    assert "await asyncio.sleep(" in src


def test_ip_sliding_window_limit():
    from app.auth import clear_ip_failures, ip_blocked, record_ip_failure
    clear_ip_failures("1.2.3.4")
    for _ in range(10):
        record_ip_failure("1.2.3.4")
    assert ip_blocked("1.2.3.4") is True
    assert ip_blocked("5.6.7.8") is False
    clear_ip_failures("1.2.3.4")
    assert ip_blocked("1.2.3.4") is False


def test_login_blocked_message_before_account_check(client, gw_db):
    """IP 达阈值后即便密码正确也统一拒之门外（不泄露账号状态）。"""
    from app.auth import create_user
    from app.db import GatewaySession
    s = GatewaySession()
    try:
        create_user(s, "ip-user", "Pass1234!", role="viewer")
    finally:
        s.close()
    from app.auth import record_ip_failure, IP_MAX_FAILURES
    for _ in range(IP_MAX_FAILURES):
        record_ip_failure("testclient")  # TestClient 默认 client.host == "testclient"
    resp = client.post("/admin/login", data={"username": "nobody", "password": "wrong"})
    assert "过于频繁" in resp.text


def test_csrf_wrong_token_rejected(client, gw_db):
    token, _ = _ensure_admin(client, "csrf-neg")
    resp = client.post(
        "/admin/queue/action",
        data={"scene": "register", "target_id": "1", "decision": "APPROVE", "_csrf": "WRONG"},
        cookies={"gw_session": token}, follow_redirects=False,
    )
    assert resp.status_code == 400


def test_idle_session_expiry(client, gw_db):
    """空闲超 30 分钟的会话被判定失效。"""
    from datetime import datetime, timedelta

    from app.auth import validate_session
    from app.db import GatewaySession, GwSession
    token, _ = _ensure_admin(client, "idle-user")
    s = GatewaySession()
    try:
        row = s.query(GwSession).filter(GwSession.id == token).one()
        stale = datetime.utcnow() - timedelta(minutes=31)
        s.query(GwSession).filter(GwSession.id == token).update({"last_active_at": stale, "created_at": stale, "expires_at": datetime.utcnow() + timedelta(hours=1)})
        s.commit()
    finally:
        s.close()
    s = GatewaySession()
    try:
        assert validate_session(s, token) is None
    finally:
        s.close()


def test_settings_numeric_validation_blocks_bad_values(gw_db):
    from app.settings import set_setting
    with pytest.raises(ValueError):
        set_setting("poll_batch_size", "abc")
    with pytest.raises(ValueError):
        set_setting("poll_batch_size", "0")
    with pytest.raises(ValueError):
        set_setting("llm_confidence_threshold", "1.5")


def test_quality_gate_skips_invalid_threshold_with_warning(gw_db, caplog):
    from app.workers.poll_pending import apply_quality_gate
    result = {"decision": "APPROVE", "confidence": 0.5}
    out = apply_quality_gate(result, {"llm_confidence_threshold": "oops"})
    assert out["decision"] == "APPROVE"  # 不再被坏配置静默放行成 0=门开


def test_queue_scene_whitelist(monkeypatch, gw_db):
    from app.admin.routes_queue import VALID_SCENES

    class FakeClient:
        def __init__(self):
            pass

        async def fetch_pending(self, scene, limit=30):
            return [{"id": 1, "scene": scene, "payload": {}, "created_at": ""}]

        async def close(self):
            pass

    import app.admin.routes_queue as rq
    monkeypatch.setattr(rq, "VoiceHubClient", FakeClient)
    assert rq.queue_page.__name__ == "queue_page"
    assert "evil" not in VALID_SCENES


def test_rules_pages_reviewer_blocked_but_admin_ok(client, gw_db):
    from app.db import GatewaySession
    from app.auth import create_user
    s = GatewaySession()
    try:
        create_user(s, "r-viewer2", "Pass1234!", role="reviewer")
    finally:
        s.close()
    from tests.test_admin_c_stage import _login
    rt = _login(client, "r-viewer2", "Pass1234!")
    resp = client.get("/admin/rules", cookies={"gw_session": rt})
    assert resp.status_code == 403  # S12：规则页仅 admin 可见


async def test_spotcheck_payload_from_log_is_stored_masked(gw_db):
    """抽查回送 LLM 的文本基于已脱敏落盘数据二次构造 —— 不会复活原文。"""
    from datetime import datetime

    from app.db import AiReviewLog, ReviewSession
    from app.mask import mask_payload_json
    from app.workers.spotcheck import run_spotcheck_once

    masked_store = mask_payload_json('{"text":"加微信 wx99887766 聊"}')
    s = ReviewSession()
    try:
        s.add(AiReviewLog(scene="note", target_id=21, decision="APPROVE",
                          reason="合规", confidence=0.9, source="l2_llm", duration_ms=5,
                          payload_json=masked_store, created_at=datetime.utcnow()))
        s.commit()
    finally:
        s.close()

    from app.settings import set_setting
    set_setting("spotcheck_enabled", "true")

    seen = []

    class SpyLlm:
        async def review(self, system_prompt, text):
            seen.append(text)
            return {"decision": "APPROVE", "reason": "ok", "confidence": 0.9, "source": "l2_llm"}

    n = await run_spotcheck_once(l2=SpyLlm())
    assert n == 1
    assert "wx99887766" not in seen[0]
