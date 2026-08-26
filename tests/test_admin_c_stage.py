"""C 阶段单测：设置热生效 / 名册 HMAC 与比对 / 抽查 / 归档清理 / 风控视图。

fixture 同时替换 gateway/review 双引擎到临时文件，并补丁所有持有模块级
session 引用的消费方；对比 A/B 阶段 fixture 的增强点即「隔离审核日志库」。
"""
import os
import tempfile

import pytest

os.environ.setdefault("ADMIN_SECRET", "test-secret-1234567890abcdef0123456789abcd")


@pytest.fixture
def gw_db(monkeypatch):
    fdg, gpath = tempfile.mkstemp(suffix=".db")
    os.close(fdg)
    fdr, rpath = tempfile.mkstemp(suffix=".db")
    os.close(fdr)
    g_url, r_url = f"sqlite:///{gpath}", f"sqlite:///{rpath}"
    monkeypatch.setattr("app.config.settings.gateway_database_url", g_url)
    monkeypatch.setattr("app.config.settings.database_url", r_url)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import app.db as _db

    g_engine = create_engine(g_url, connect_args={"check_same_thread": False})
    r_engine = create_engine(r_url, connect_args={"check_same_thread": False})
    g_sm = sessionmaker(bind=g_engine, autoflush=False, expire_on_commit=False)
    r_sm = sessionmaker(bind=r_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(_db, "gateway_engine", g_engine)
    monkeypatch.setattr(_db, "review_engine", r_engine)
    monkeypatch.setattr(_db, "GatewaySession", g_sm)
    monkeypatch.setattr(_db, "ReviewSession", r_sm)
    monkeypatch.setattr(_db, "SessionLocal", r_sm)

    import importlib
    for mod_name in [
        "app.admin.auth_routes", "app.admin.bootstrap", "app.admin.decorators",
        "app.admin.routes_providers", "app.admin.routes_queue",
        "app.admin.routes_rules", "app.admin.routes_logs",
        "app.admin.routes_dashboard", "app.admin.routes_roster",
        "app.admin.routes_settings", "app.admin.routes_spotcheck",
        "app.admin.routes_risk", "app.workers.poll_pending",
    ]:
        try:
            mod = importlib.import_module(mod_name)
            for attr, val in (("GatewaySession", g_sm), ("ReviewSession", r_sm), ("SessionLocal", r_sm)):
                if hasattr(mod, attr):
                    monkeypatch.setattr(mod, attr, val)
        except Exception:
            pass

    from app.db import Base
    Base.metadata.create_all(bind=g_engine)
    Base.metadata.create_all(bind=r_engine)

    # 测试专用 worker 直读入口也重绑（cleanup_once 等函数内仍走惰性导入，已覆盖）
    yield {"g_url": g_url, "r_url": r_url, "gw": g_sm, "rev": r_sm}

    for p in (gpath, rpath):
        try:
            os.unlink(p)
        except OSError:
            pass


@pytest.fixture
def client(gw_db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _login(client, username, password) -> str:
    resp = client.post("/admin/login", data={"username": username, "password": password}, follow_redirects=False)
    assert resp.status_code == 303, f"login failed: {resp.status_code}"
    return resp.cookies.get("gw_session")


def _csrf_for(token: str) -> str:
    from app.db import GatewaySession, GwSession
    s = GatewaySession()
    try:
        return s.query(GwSession).filter_by(id=token).one().csrf_token
    finally:
        s.close()


def _ensure_admin(client, username="c-admin"):
    from app.db import GatewaySession, GwUser
    from app.auth import create_user
    session = GatewaySession()
    try:
        if session.query(GwUser).filter_by(username=username).count() == 0:
            create_user(session, username, "Pass1234!", role="admin")
    finally:
        session.close()
    token = _login(client, username, "Pass1234!")
    return token, _csrf_for(token)


# ---------------- 设置服务（C1/C2） ----------------
def test_get_settings_defaults_on_empty_db(gw_db):
    from app.settings import get_setting
    assert get_setting("review_scenes") == "register,note"
    assert get_setting("log_retention_days") == "180"


def test_set_and_clear_override(gw_db):
    from app.settings import get_setting, set_setting
    set_setting("review_scenes", "note")
    assert get_setting("review_scenes") == "note"
    set_setting("review_scenes", "")  # 清除覆盖回退 env/默认
    assert get_setting("review_scenes") == "register,note"


def test_priority_db_over_env(monkeypatch, gw_db):
    from app.config import settings as env_settings
    from app.settings import get_setting, set_setting
    monkeypatch.setattr(env_settings, "poll_batch_size", 9)
    set_setting("poll_batch_size", "33")
    assert get_setting("poll_batch_size") == "33"
    set_setting("poll_batch_size", "")
    assert get_setting("poll_batch_size") == "9"


def test_unknown_key_rejected(gw_db):
    from app.settings import get_setting, set_setting
    with pytest.raises(KeyError):
        get_setting("not_a_key")
    with pytest.raises(KeyError):
        set_setting("evil_key", "x")


def test_sources_flags(gw_db):
    from app.settings import setting_sources, set_setting
    src = setting_sources()
    assert src["review_scenes"] in ("env", "default")
    set_setting("archive_keep", "3")
    assert setting_sources()["archive_keep"] == "db"


def test_parse_bool():
    from app.settings import parse_bool
    assert parse_bool("true") and parse_bool("1") and parse_bool("ON")
    assert not parse_bool("") and not parse_bool("false") and not parse_bool("no")


# ---------------- worker 配置消费 ----------------
def test_get_scenes_freeze_filters_register():
    from app.workers.poll_pending import get_scenes
    cfg = {"review_scenes": "register,note", "register_channel_frozen": "true"}
    assert get_scenes(cfg) == ["note"]
    cfg["register_channel_frozen"] = "false"
    assert get_scenes(cfg) == ["register", "note"]


def test_quality_gate_threshold():
    from app.workers.poll_pending import apply_quality_gate
    cfg = {"llm_confidence_threshold": "0.60"}
    low = apply_quality_gate({"decision": "APPROVE", "confidence": 0.5, "reason": "ok"}, cfg)
    assert low["decision"] == "REVIEW" and "阈值" in low["reason"]
    none_conf = apply_quality_gate({"decision": "APPROVE", "confidence": None}, cfg)
    assert none_conf["decision"] == "APPROVE"
    reject = apply_quality_gate({"decision": "REJECT", "confidence": 0.2}, cfg)
    assert reject["decision"] == "REJECT"


async def test_poll_quality_gate_end_to_end(gw_db):
    from app.reviewers.l1_rules import L1RulesReviewer
    from app.workers.poll_pending import poll_once

    class C:
        def __init__(self):
            self.submitted = []

        async def fetch_pending(self, scene, limit=20):
            return [{"id": 7, "scene": "note", "payload": {"text": "好"}}] if scene == "note" else []

        async def submit_result(self, **kw):
            self.submitted.append(kw)

    class LowConfLlm:
        async def review(self, *_a, **_k):
            return {"decision": "APPROVE", "reason": "合规", "confidence": 0.2, "source": "l2_llm"}

    c = C()
    await poll_once(c, L1RulesReviewer(), LowConfLlm(), None, state={}, cfg={"llm_confidence_threshold": "0.8"})
    assert len(c.submitted) == 1
    assert c.submitted[0]["decision"] == "REVIEW"
    assert "阈值" in c.submitted[0]["reason"]


async def test_language_whitelist_route_to_review(gw_db):
    from app.reviewers.l1_rules import L1RulesReviewer
    from app.reviewers.language_detector import LanguageDetector
    from app.workers.poll_pending import poll_once

    class FakeLlm:
        async def complete_json(self, *_a, **_k):
            return {"language": "日文", "confidence": 0.95}

    class C:
        def __init__(self):
            self.submitted = []

        async def fetch_pending(self, scene, limit=20):
            return [{"id": 3, "scene": "language", "payload": {"title": "X"}}]

        async def submit_result(self, **kw):
            self.submitted.append(kw)

    detector = LanguageDetector(FakeLlm(), None)
    c = C()
    await poll_once(c, L1RulesReviewer(), FakeLlm(), detector,
                    state={}, cfg={"language_whitelist": "中文,英文", "review_scenes": "language"})
    assert c.submitted[0]["decision"] == "REVIEW"
    assert "白名单" in c.submitted[0]["reason"]


# ---------------- 名册（C3 + SPEC S12） ----------------
_CSV_OK = "学号,姓名,年级,班级\r\n20240101,张三,高一,1班\r\n20240102,李四,,\r\n".encode("utf-8")


def test_parse_csv_ok_and_fields():
    from app.roster import parse_roster_csv
    rows = parse_roster_csv(_CSV_OK)
    assert len(rows) == 2
    assert rows[0]["student_no"] == "20240101" and rows[0]["name"] == "张三"
    assert rows[0]["grade"] == "高一" and rows[1]["class"] == ""


def test_parse_csv_oversize_rejected():
    from app.roster import MAX_CSV_BYTES, RosterImportError, parse_roster_csv
    header = "学号,姓名\n".encode("utf-8")
    big = header + b"1,a\n" * (MAX_CSV_BYTES // 4 + 1024)
    assert len(big) > MAX_CSV_BYTES
    with pytest.raises(RosterImportError, match="5MB"):
        parse_roster_csv(big)


def test_parse_csv_bad_encoding():
    from app.roster import RosterImportError, parse_roster_csv
    with pytest.raises(RosterImportError, match="UTF-8"):
        parse_roster_csv(b"\xff\xfe\x00bad")


def test_parse_csv_missing_header():
    from app.roster import RosterImportError, parse_roster_csv
    with pytest.raises(RosterImportError, match="缺少.*学号"):
        parse_roster_csv("no,name\n1,x".encode("utf-8"))


def test_parse_csv_duplicate_student_no():
    from app.roster import RosterImportError, parse_roster_csv
    dup = "学号,姓名\n20240101,A\n20240101,B".encode("utf-8")
    with pytest.raises(RosterImportError, match="重复"):
        parse_roster_csv(dup)


def test_extract_student_no_bounds():
    from app.roster import extract_student_no
    assert extract_student_no("我是20240101张三") == "20240101"
    assert extract_student_no("短号 12345") is None
    assert extract_student_no(None) is None


def test_import_rows_idempotent(gw_db):
    from app.roster import import_rows, parse_roster_csv
    rows = parse_roster_csv(_CSV_OK)
    session = gw_db["gw"]()
    try:
        added, updated = import_rows(session, rows, actor="t")
        assert (added, updated) == (2, 0)
        rows[0]["name"] = "张小三"
        added2, updated2 = import_rows(session, rows, actor="t")
        assert (added2, updated2) == (0, 2)
    finally:
        session.close()


def test_check_register_note_branches(gw_db):
    from app.auth import create_user  # noqa: F401 确保模块可用性一致性
    from app.db import GwRoster
    from app.roster import check_register_note, import_rows, parse_roster_csv
    session = gw_db["gw"]()
    try:
        import_rows(session, parse_roster_csv(_CSV_OK), actor="t")

        # 名册空判断不受影响后：命中且姓名一致
        ok, why = check_register_note({"remark": "学号20240101", "name": "张三"})
        assert ok is True and why == ""
        # 命中但姓名不一致
        ok, why = check_register_note({"remark": "我的编号20240102谢谢", "name": "王五"})
        assert ok is False and "不一致" in why
        # 未命中（疑编造）
        ok, why = check_register_note({"remark": "学号99999999", "name": "张三"})
        assert ok is False and "不在名册" in why
        # 无候选学号：不干预
        ok, why = check_register_note({"remark": "喜欢唱歌跳舞", "name": "张三"})
        assert ok is True
        # 姓名为空同样转人工
        ok, why = check_register_note({"remark": "学号20240101", "name": ""})
        assert ok is False
        n = session.query(GwRoster).count()
        assert n == 2
    finally:
        session.close()


async def test_poll_register_roster_flip_to_review(gw_db):
    """L2 APPROVE 后备注学号不在名册 → 改判 REVIEW，source=roster_check。"""
    from app.reviewers.l1_rules import L1RulesReviewer
    from app.roster import import_rows, parse_roster_csv
    session = gw_db["gw"]()
    try:
        import_rows(session, parse_roster_csv(_CSV_OK), actor="t")
    finally:
        session.close()

    from app.workers.poll_pending import poll_once

    class OkLlm:
        async def review(self, *_a, **_k):
            return {"decision": "APPROVE", "reason": "合规", "confidence": 0.95, "source": "l2_llm"}

    class C:
        def __init__(self):
            self.submitted = []

        async def fetch_pending(self, scene, limit=20):
            if scene != "register":
                return []
            return [
                {"id": 1, "scene": "register", "payload": {"username": "u1", "name": "赵六", "remark": "学号88888888"}},
                {"id": 2, "scene": "register", "payload": {"username": "u2", "name": "张三", "remark": "20240101"}},
            ]

        async def submit_result(self, **kw):
            self.submitted.append(kw)

    c = C()
    await poll_once(c, L1RulesReviewer(), OkLlm(), None, state={}, cfg={})
    by_id = {s["target_id"]: s for s in c.submitted}
    assert by_id[1]["decision"] == "REVIEW" and by_id[1]["source"] == "roster_check"
    assert by_id[2]["decision"] == "APPROVE"


def test_roster_routes_roundtrip(client, gw_db):
    token, csrf = _ensure_admin(client)
    resp = client.get("/admin/roster", cookies={"gw_session": token})
    assert resp.status_code == 200 and "导入名册" in resp.text

    resp = client.post(
        "/admin/roster/preview",
        data={"_csrf": csrf},
        files={"file": ("roster.csv", _CSV_OK, "text/csv")},
        cookies={"gw_session": token},
    )
    assert resp.status_code == 200
    assert "确认导入全部 2 行" in resp.text and "20240101" in resp.text

    import base64
    b64 = base64.b64encode(_CSV_OK).decode("ascii")
    resp = client.post(
        "/admin/roster/import",
        data={"_csrf": csrf, "data_b64": b64},
        cookies={"gw_session": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    from app.db import GatewaySession, GwAuditLog, GwRoster
    session = GatewaySession()
    try:
        assert session.query(GwRoster).count() == 2
        audits = session.query(GwAuditLog).filter_by(action="roster_import").all()
        assert len(audits) == 1 and "added=2" in audits[0].after_json
    finally:
        session.close()


# ---------------- 抽查（C4/C5） ----------------
async def test_spotcheck_disabled_noop():
    from app.workers.spotcheck import run_spotcheck_once
    assert await run_spotcheck_once() == 0


def _seed_approved_log(rev_sm, scene="register", target_id=11):
    from datetime import datetime
    from app.db import AiReviewLog
    session = rev_sm()
    try:
        session.add(AiReviewLog(
            scene=scene, target_id=target_id, decision="APPROVE",
            reason="合规", confidence=0.9, model="l2_llm", source="l2_llm",
            duration_ms=10,
            payload_json='{"remark":"学号20240101","name":"张三"}' if scene == "register" else '{"text":"ok"}',
            created_at=datetime.utcnow(),
        ))
        session.commit()
    finally:
        session.close()


async def test_spotcheck_once_records_log(gw_db, monkeypatch):
    from app.settings import set_setting
    set_setting("spotcheck_enabled", "true")
    _seed_approved_log(gw_db["rev"])

    from app.workers.spotcheck import run_spotcheck_once

    class RejectLlm:
        async def review(self, system_prompt, text):
            return {"decision": "REJECT", "reason": "复审发现违规词", "confidence": 0.8, "source": "l2_llm"}

    checked = await run_spotcheck_once(l2=RejectLlm())
    assert checked == 1
    from app.db import GwSpotcheckLog, ReviewSession
    session = ReviewSession()
    try:
        row = session.query(GwSpotcheckLog).one()
        assert row.original_decision == "APPROVE" and row.recheck_decision == "REJECT"
        assert row.reviewed_by == "system-spotcheck"
    finally:
        session.close()


async def test_spotcheck_dedupes_same_target(gw_db, monkeypatch):
    from app.settings import set_setting
    set_setting("spotcheck_enabled", "true")
    for tid in (11, 11, 11):
        _seed_approved_log(gw_db["rev"], target_id=tid)

    from app.workers.spotcheck import run_spotcheck_once

    class ApproveLlm:
        async def review(self, *_a, **_k):
            return {"decision": "APPROVE", "reason": "一致", "confidence": 0.9, "source": "l2_llm"}

    assert await run_spotcheck_once(l2=ApproveLlm()) == 1


def test_spotcheck_page_lists_needs_human(client, gw_db):
    token, _ = _ensure_admin(client, "sp-admin")
    from datetime import datetime
    from app.db import GwSpotcheckLog, ReviewSession
    session = ReviewSession()
    try:
        session.add(GwSpotcheckLog(
            scene="note", target_id=99, original_decision="APPROVE",
            recheck_decision="REJECT", confidence=0.7, model="l2_llm",
            reason="抽查发现违规", reviewed_by="system-spotcheck",
            created_at=datetime.utcnow(),
        ))
        session.commit()
    finally:
        session.close()
    resp = client.get("/admin/spotcheck", cookies={"gw_session": token})
    assert resp.status_code == 200
    assert "待人工复核" in resp.text and "REJECT" in resp.text


# ---------------- 归档与清理（C6/C8） ----------------
def test_sqlite_path_parsing():
    from app.workers.archive import sqlite_path
    p = sqlite_path("sqlite:///./data/x.db")
    assert str(p).endswith("x.db")
    assert sqlite_path("postgresql://u:p@h/db") is None
    assert sqlite_path("") is None


def test_archive_now_creates_and_prunes(gw_db, tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path

    from app.config import settings
    from app.workers.archive import archive_now

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    for name in ("ai_review.db", "gateway.db"):
        conn = sqlite3.connect(str(src_dir / name))
        try:
            conn.execute("CREATE TABLE t(x)")
            conn.commit()
        finally:
            conn.close()
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{(src_dir / 'ai_review.db').as_posix()}")
    monkeypatch.setattr(settings, "gateway_database_url", f"sqlite:///{(src_dir / 'gateway.db').as_posix()}")

    dest = tmp_path / "archive"
    dest.mkdir()
    # 同 stem 的「旧」快照：加入后按 keep=1 应被最新快照淘汰
    fake_old = dest / "ai_review_20200101T000000Z.db"
    fake_old.write_bytes(b"")

    created = archive_now(dest_dir=str(dest), keep=1)
    names = {Path(c).name for c in created}
    assert any(n.startswith("ai_review_") and n != "ai_review_20200101T000000Z.db" for n in names)
    survivors = {p.name for p in dest.glob("*.db")}
    assert "ai_review_20200101T000000Z.db" not in survivors  # 超 keep 被清理
    # gateway 快照独立成组不受影响
    assert sum(1 for s in survivors if s.startswith("gateway_")) == 1


def test_cleanup_once_respects_retention(gw_db):
    from datetime import datetime, timedelta
    from app.db import AiReviewLog, ReviewSession
    session = ReviewSession()
    try:
        old = datetime.utcnow() - timedelta(days=400)
        fresh = datetime.utcnow()
        session.add(AiReviewLog(scene="note", target_id=1, decision="APPROVE", created_at=old))
        session.add(AiReviewLog(scene="note", target_id=2, decision="APPROVE", created_at=fresh))
        session.commit()
    finally:
        session.close()

    from app.workers.cleanup import cleanup_once
    deleted = cleanup_once(retention_days=180)
    assert deleted == 1
    session = ReviewSession()
    try:
        remaining = [r.target_id for r in session.query(AiReviewLog).all()]
        assert remaining == [2]
    finally:
        session.close()


# ---------------- 风控视图（C7） ----------------
def _seed_register_logs(rev_sm):
    from datetime import datetime
    from app.db import AiReviewLog
    rows = [
        ("APPROVE", "加我微信aaa"), ("APPROVE", "加我微信bbb"),
        ("APPROVE", "随便聊聊今天天气"), ("REVIEW", "加我微信ccc"),
        ("REJECT", "独立内容甲"), ("REJECT", "独立内容乙"),
    ]
    session = rev_sm()
    try:
        for i, (d, remark) in enumerate(rows, start=1):
            session.add(AiReviewLog(
                scene="register", target_id=i, decision=d, reason="x",
                payload_json=f'{{"remark":"{remark}","name":"n{i}"}}',
                created_at=datetime.utcnow(),
            ))
        session.commit()
    finally:
        session.close()


def test_risk_snapshot_ratios_and_cluster(gw_db):
    _seed_register_logs(gw_db["rev"])
    from app.admin.routes_risk import risk_snapshot
    snap = risk_snapshot(limit=500)
    assert snap["total_window"] == 6
    assert snap["approve_pct"] == "50.0%" and snap["reject_pct"] == "33.3%" and snap["review_pct"] == "16.7%"
    keys = [c["key"] for c in snap["clusters"]]
    assert any("加我微信" in k for k in keys)
    cluster = next(c for c in snap["clusters"] if "加我微信" in c["key"])
    assert cluster["count"] == 3


def test_freeze_toggle_and_audit(client, gw_db):
    token, csrf = _ensure_admin(client, "risk-admin")
    resp = client.post("/admin/risk/freeze", data={"_csrf": csrf, "freeze": "true"}, cookies={"gw_session": token}, follow_redirects=False)
    assert resp.status_code == 303
    from app.db import GwAuditLog, GatewaySession
    from app.settings import get_setting
    assert get_setting("register_channel_frozen") == "true"
    session = GatewaySession()
    try:
        audit = session.query(GwAuditLog).filter_by(action="register_channel_freeze").one()
        assert audit.after_json == '{"register_channel_frozen": "true"}'
    finally:
        session.close()
    resp = client.get("/admin/risk", cookies={"gw_session": token})
    assert "已冻结" in resp.text
    # 反向解冻
    resp = client.post("/admin/risk/freeze", data={"_csrf": csrf}, cookies={"gw_session": token}, follow_redirects=False)
    assert resp.status_code == 303
    assert get_setting("register_channel_frozen") == "false"


# ---------------- 设置页路由 + 角色矩阵 ----------------
def test_settings_save_roundtrip_and_audit(client, gw_db):
    token, csrf = _ensure_admin(client, "set-admin")
    resp = client.get("/admin/settings", cookies={"gw_session": token})
    assert resp.status_code == 200 and "保存全部" in resp.text

    form = {"_csrf": csrf, "poll_interval_seconds": "45", "review_scenes": "register"}
    resp = client.post("/admin/settings/save", data=form, cookies={"gw_session": token}, follow_redirects=False)
    assert resp.status_code == 303 and "saved=1" in resp.headers["location"]

    from app.db import GatewaySession, GwAuditLog, GwSetting
    from app.settings import get_setting
    assert get_setting("poll_interval_seconds") == "45"
    # 未勾选布尔 → 显式 false；未提交的其它键不动
    assert get_setting("register_channel_frozen") == "false"
    session = GatewaySession()
    try:
        audit = session.query(GwAuditLog).filter_by(action="settings_save").one()
        assert '"poll_interval_seconds"' in audit.before_json
        kv = {r.key: r.value for r in session.query(GwSetting).all()}
        assert kv["poll_interval_seconds"] == "45"
        assert "l3_enabled" not in kv or kv["l3_enabled"] == "false"
    finally:
        session.close()


def test_role_matrix_blocks_viewer_from_admin_pages(client, gw_db):
    from app.auth import create_user
    from app.db import GatewaySession, GwUser
    session = GatewaySession()
    try:
        if session.query(GwUser).filter_by(username="c-viewer").count() == 0:
            create_user(session, "c-viewer", "Pass1234!", role="viewer")
    finally:
        session.close()
    token = _login(client, "c-viewer", "Pass1234!")

    for url in ("/admin/settings", "/admin/roster", "/admin/risk"):
        resp = client.get(url, cookies={"gw_session": token})
        assert resp.status_code in (401, 403), f"{url}: {resp.status_code}"

    # 队列写回：带合法表单字段（表单校验先于鉴权时也可能 422，同样视为拦截）
    resp = client.post(
        "/admin/queue/action",
        data={"scene": "register", "target_id": "1", "decision": "APPROVE", "reason": "", "_csrf": "x"},
        cookies={"gw_session": token},
        follow_redirects=False,
    )
    assert resp.status_code in (401, 403), resp.status_code

    # reviewer 可看抽查页但不可进设置
    session = GatewaySession()
    try:
        create_user(session, "c-reviewer", "Pass1234!", role="reviewer")
    finally:
        session.close()
    rt = _login(client, "c-reviewer", "Pass1234!")
    assert client.get("/admin/spotcheck", cookies={"gw_session": rt}).status_code == 200
    assert client.get("/admin/settings", cookies={"gw_session": rt}).status_code == 403
