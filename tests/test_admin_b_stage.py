"""B 阶段单测：人工复核写回 / 队列路由 / 规则重载 / 日志检索。"""
import os
import tempfile

import pytest

os.environ.setdefault("ADMIN_SECRET", "test-secret-1234567890abcdef0123456789abcd")


@pytest.fixture
def gateway_db_url(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    url = f"sqlite:///{path}"
    monkeypatch.setattr("app.config.settings.gateway_database_url", url)
    import app.db as _db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    new_engine = create_engine(url, connect_args={"check_same_thread": False})
    new_factory = sessionmaker(bind=new_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(_db, "gateway_engine", new_engine)
    # GatewaySession 是 class factory；setattr class 会让它指向新 factory
    class _NewFactory:
        def __call__(self, *a, **kw):
            return new_factory(*a, **kw)
    monkeypatch.setattr(_db, "GatewaySession", _NewFactory())
    import importlib
    for mod_name in [
        "app.admin.auth_routes",
        "app.admin.bootstrap",
        "app.admin.decorators",
        "app.admin.routes_providers",
        "app.admin.routes_queue",
        "app.admin.routes_rules",
        "app.admin.routes_logs",
        "app.admin.routes_dashboard",
    ]:
        try:
            mod = importlib.import_module(mod_name)
            monkeypatch.setattr(mod, "GatewaySession", _NewFactory())
        except Exception:
            pass
    from app.db import Base
    Base.metadata.create_all(bind=new_engine)
    yield url
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def test_app_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _login(client, username, password) -> str:
    resp = client.post("/admin/login", data={"username": username, "password": password}, follow_redirects=False)
    assert resp.status_code == 303, f"login failed: {resp.status_code} {resp.text[:200]}"
    return resp.cookies.get("gw_session")


def _csrf_for(session_token) -> str:
    """按会话 token 取该会行的 CSRF——与模板 `gw_session.csrf_token` 渲染行为一致。"""
    from app.db import GatewaySession, GwSession
    session = GatewaySession()
    try:
        s = session.query(GwSession).filter_by(id=session_token).one()
        return s.csrf_token
    finally:
        session.close()


def _ensure_admin(username="admin1", password="Pass1234!") -> None:
    from app.db import init_db, GatewaySession, GwUser
    from app.auth import create_user
    init_db()
    session = GatewaySession()
    try:
        if session.query(GwUser).filter_by(username=username).count() > 0:
            return
        create_user(session, username, password, role="admin")
    finally:
        session.close()


def test_review_action_calls_voicehub_submit(monkeypatch, test_app_client, gateway_db_url):
    """人工写回调用 VoiceHubClient.submit_result，reason 标注「人工」。"""
    _ensure_admin()
    from app.voicehub_client import VoiceHubClient
    submitted = {}

    async def fake_submit(self, scene, target_id, decision, reason, confidence, model, source, duration_ms):
        submitted.update({"scene": scene, "decision": decision, "reason": reason, "target_id": target_id})

    monkeypatch.setattr(VoiceHubClient, "submit_result", fake_submit)

    token = _login(test_app_client, "admin1", "Pass1234!")
    csrf = _csrf_for(token)

    resp = test_app_client.post(
        "/admin/queue/action",
        data={"scene": "register", "target_id": "42", "decision": "APPROVE", "reason": "测试通过", "_csrf": csrf},
        cookies={"gw_session": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"action failed: {resp.status_code} {resp.text[:200]}"
    assert submitted == {
        "scene": "register",
        "decision": "APPROVE",
        "reason": "人工复核：测试通过",
        "target_id": 42,
    }


def test_logs_route_renders(gateway_db_url, test_app_client):
    """viewer 视图 payload 脱敏。"""
    from app.db import init_db, AiReviewLog, ReviewSession, GatewaySession, GwUser
    from app.auth import create_user
    from datetime import datetime
    init_db()
    session = ReviewSession()
    try:
        session.add(AiReviewLog(
            scene="register", target_id=1, decision="APPROVE",
            reason="合规", confidence=0.9, model="m", source="l2_llm",
            duration_ms=120, payload_json='{"name":"张三"}',
            created_at=datetime.utcnow(),
        ))
        session.commit()
    finally:
        session.close()
    session = GatewaySession()
    try:
        if session.query(GwUser).filter_by(username="v").count() == 0:
            create_user(session, "v", "Pass1234!", role="viewer")
    finally:
        session.close()
    token = _login(test_app_client, "v", "Pass1234!")
    resp = test_app_client.get("/admin/logs", cookies={"gw_session": token})
    assert resp.status_code == 200, f"logs failed: {resp.status_code}"
    assert "张*" in resp.text


def test_logs_admin_sees_raw(gateway_db_url, test_app_client):
    """admin 视图不应脱敏。"""
    from app.db import init_db, AiReviewLog, ReviewSession, GatewaySession, GwUser
    from app.auth import create_user
    from datetime import datetime
    init_db()
    session = ReviewSession()
    try:
        session.add(AiReviewLog(
            scene="register", target_id=2, decision="APPROVE",
            reason="合规", confidence=0.9, model="m", source="l2_llm",
            duration_ms=80, payload_json='{"name":"张三"}',
            created_at=datetime.utcnow(),
        ))
        session.commit()
    finally:
        session.close()
    session = GatewaySession()
    try:
        if session.query(GwUser).filter_by(username="a").count() == 0:
            create_user(session, "a", "Pass1234!", role="admin")
    finally:
        session.close()
    token = _login(test_app_client, "a", "Pass1234!")
    resp = test_app_client.get("/admin/logs", cookies={"gw_session": token})
    assert resp.status_code == 200
    assert "张三" in resp.text


def test_rules_reload_loads_db_rules(gateway_db_url):
    """reload_runtime 从 DB 加载自定义规则到内存。"""
    from app.db import init_db, GatewaySession, GwRule
    import json as _json
    init_db()
    session = GatewaySession()
    try:
        session.add(GwRule(
            name="custom_test", pattern=r"\bfoo\b", label="自定义",
            skip_scenes_json=_json.dumps(["register"]), enabled=True,
        ))
        session.commit()
    finally:
        session.close()
    from app.admin.routes_rules import reload_runtime
    rt = reload_runtime()
    names = [n for (n, _, _, _) in rt._patterns]
    assert "custom_test" in names


def test_rule_test_endpoint_matches(gateway_db_url, test_app_client):
    """命中测试端点。"""
    from app.db import init_db, GatewaySession, GwUser
    from app.auth import create_user
    init_db()
    session = GatewaySession()
    try:
        if session.query(GwUser).filter_by(username="a2").count() == 0:
            create_user(session, "a2", "Pass1234!", role="admin")
    finally:
        session.close()
    token = _login(test_app_client, "a2", "Pass1234!")
    csrf = _csrf_for(token)
    resp = test_app_client.post(
        "/admin/rules/test",
        data={"pattern": r"\d{6,}", "text": "123456", "skip_scenes": "register", "_csrf": csrf},
        cookies={"gw_session": token},
    )
    assert resp.status_code == 200, f"rule test failed: {resp.status_code} {resp.text[:200]}"
    assert "命中" in resp.text


def test_voicehub_submit_result_invoked_with_review_decision(monkeypatch, gateway_db_url):
    """人工 REVIEW 写回调用。"""
    from app.db import init_db, GatewaySession, GwUser
    from app.auth import create_user, create_session as cs
    init_db()
    session = GatewaySession()
    try:
        if session.query(GwUser).filter_by(username="a3").count() == 0:
            u = create_user(session, "a3", "Pass1234!", role="admin")
        else:
            u = session.query(GwUser).filter_by(username="a3").one()
        token, csrf = cs(session, u.id, "127.0.0.1", "test")
    finally:
        session.close()
    from app.voicehub_client import VoiceHubClient
    captured = {}

    async def fake_submit(self, scene, target_id, decision, reason, **kw):
        captured.update({"scene": scene, "decision": decision, "reason": reason, "target_id": target_id})

    monkeypatch.setattr(VoiceHubClient, "submit_result", fake_submit)

    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)
    resp = client.post(
        "/admin/queue/action",
        data={"scene": "note", "target_id": "99", "decision": "REVIEW", "_csrf": csrf},
        cookies={"gw_session": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"action failed: {resp.status_code} {resp.text[:200]}"
    assert captured["decision"] == "REVIEW"
    assert captured["scene"] == "note"
    assert "人工" in captured["reason"]