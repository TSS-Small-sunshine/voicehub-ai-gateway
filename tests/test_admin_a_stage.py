"""A 阶段单测：mask / auth / security / providers service / llm_client。"""
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
    # 强制重建 gateway_engine（模块级 engine 在 import 时已绑定旧 url）
    import app.db as _db
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    new_engine = create_engine(url, connect_args={"check_same_thread": False})
    new_session = sessionmaker(bind=new_engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(_db, "gateway_engine", new_engine)
    monkeypatch.setattr(_db, "GatewaySession", new_session)
    yield url
    try:
        os.unlink(path)
    except OSError:
        pass


def test_mask_phone():
    from app.mask import mask_pii
    assert mask_pii("联系我 13812345678") == "联系我 138****5678"
    assert mask_pii("电话 19912345678") == "电话 199****5678"


def test_mask_qq_keeps_short_unchanged():
    from app.mask import mask_pii
    out = mask_pii("加我 QQ 123456789")
    assert "123" in out and "89" in out and "*" in out
    # 5 位以下不动
    assert mask_pii("数字 12345") == "数字 12345"


def test_mask_qq_takes_precedence_over_student_no():
    from app.mask import mask_pii
    # QQ 8 位
    out = mask_pii("QQ: 12345678")
    assert "123" in out and "78" in out and "*" in out


def test_mask_url():
    from app.mask import mask_pii
    assert mask_pii("详见 https://example.com/foo") == "详见 [URL]"
    assert mask_pii("www.example.com/bar") == "[URL]"


def test_mask_idcard():
    from app.mask import mask_pii
    out = mask_pii("身份证 110105199001011234")
    assert "1101" in out and "1234" in out and "**********" in out


def test_mask_student_no():
    from app.mask import mask_pii
    out = mask_pii("学号 2024010101")
    assert "20" in out and "01" in out and "*" in out


def test_mask_field_name():
    from app.mask import mask_field_name
    assert mask_field_name("张三") == "张*"
    assert mask_field_name("欧阳娜娜") == "欧***"
    assert mask_field_name("") == ""
    assert mask_field_name("A") == "A"


def test_mask_field_student_no():
    from app.mask import mask_field_student_no
    assert mask_field_student_no("20240101") == "20****01"
    assert mask_field_student_no("") == ""
    assert mask_field_student_no("12345") == "12345"


def test_security_hmac_deterministic():
    from app.security import hmac_student_no
    a = hmac_student_no("20240101")
    b = hmac_student_no("20240101")
    assert a == b
    assert hmac_student_no("20240102") != a
    assert len(a) == 64


def test_security_fernet_roundtrip():
    from app.security import fernet_encrypt, fernet_decrypt
    plain = "sk-test-1234567890"
    enc = fernet_encrypt(plain)
    assert enc != plain
    assert fernet_decrypt(enc) == plain


def test_security_fernet_raises_without_admin_secret(monkeypatch):
    from app import security
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    with pytest.raises(RuntimeError):
        security.fernet_encrypt("anything")


def test_security_hmac_raises_without_admin_secret(monkeypatch):
    from app import security
    from app.config import settings
    monkeypatch.setattr(settings, "admin_secret", "")
    with pytest.raises(RuntimeError):
        security.hmac_student_no("123")


def test_security_csrf_sign_verify():
    from app.security import csrf_sign, csrf_verify
    s = csrf_sign("token-abc")
    assert csrf_verify(s) == "token-abc"


def test_security_totp():
    from app.security import new_totp_secret, verify_totp
    import pyotp
    s = new_totp_secret()
    code = pyotp.TOTP(s).now()
    assert verify_totp(s, code) is True
    assert verify_totp(s, "000000") is False


def test_provider_seed_and_list(gateway_db_url):
    from app.db import init_db, GatewaySession
    from app.providers import seed_default_providers, list_providers, get_default_provider, create_provider
    init_db()
    session = GatewaySession()
    try:
        seed_default_providers(session)
        ps = list_providers(session)
        assert len(ps) >= 8
        assert get_default_provider(session) is None  # 默认未启用
        p = create_provider(session, name="测试", base_url="http://x", model="x", api_key="k", enabled=True, priority=1)
        assert p.api_key_encrypted
        assert p.enabled
        default = get_default_provider(session)
        assert default is not None and default.name == "测试"
    finally:
        session.close()


def test_provider_crud(gateway_db_url):
    from app.db import init_db, GatewaySession
    from app.providers import create_provider, update_provider, delete_provider, list_providers, decrypt_key
    init_db()
    session = GatewaySession()
    try:
        p = create_provider(session, name="T", base_url="http://x", model="m", api_key="secret-key-123")
        assert decrypt_key(p.api_key_encrypted) == "secret-key-123"
        update_provider(session, p.id, base_url="http://y", priority=10)
        session.refresh(p)
        assert p.base_url == "http://y" and p.priority == 10
        assert delete_provider(session, p.id) is True
        assert list_providers(session) == []
    finally:
        session.close()


def test_auth_user_flow(gateway_db_url):
    from app.db import init_db, GatewaySession, GwUser
    from app.auth import (
        create_user, authenticate, create_session, validate_session, change_password, revoke_session
    )
    from app.security import verify_password
    init_db()
    session = GatewaySession()
    try:
        user = create_user(session, "alice", "Password123!", role="admin")
        assert authenticate(session, "alice", "Password123!") is not None
        assert authenticate(session, "alice", "wrong") is None
        for _ in range(5):
            authenticate(session, "alice", "wrong")
        assert authenticate(session, "alice", "Password123!") is None
        user.locked_until = None
        user.failed_logins = 0
        session.commit()
        token, csrf = create_session(session, user.id, "127.0.0.1", "test-agent")
        result = validate_session(session, token)
        assert result is not None
        u, s = result
        assert u.username == "alice" and s.csrf_token == csrf
        revoke_session(session, token)
        assert validate_session(session, token) is None
        change_password(session, user, "NewPassword456!")
        assert verify_password("NewPassword456!", user.password_hash)
    finally:
        session.close()


def test_admin_bootstrap_creates_admin(gateway_db_url, monkeypatch):
    from app.db import init_db, GatewaySession, GwUser
    from app.admin.bootstrap import ensure_admin
    from app.config import settings
    monkeypatch.setattr(settings, "admin_init_user", "firstadmin")
    monkeypatch.setattr(settings, "admin_init_pass", "InitPass123!")
    init_db()
    ensure_admin()
    session = GatewaySession()
    try:
        users = session.query(GwUser).all()
        assert len(users) == 1
        assert users[0].username == "firstadmin"
        assert users[0].role == "admin"
        assert users[0].must_change_password is True
    finally:
        session.close()
    ensure_admin()
    session = GatewaySession()
    try:
        assert session.query(GwUser).count() == 1
    finally:
        session.close()


def test_admin_bootstrap_noop_when_no_init(gateway_db_url, monkeypatch):
    """无 ADMIN_INIT_USER/PASS 时不创建用户（仅告警）。"""
    from app.db import init_db, GatewaySession, GwUser
    from app.admin.bootstrap import ensure_admin
    from app.config import settings
    monkeypatch.setattr(settings, "admin_init_user", "")
    monkeypatch.setattr(settings, "admin_init_pass", "")
    init_db()
    ensure_admin()
    session = GatewaySession()
    try:
        assert session.query(GwUser).count() == 0
    finally:
        session.close()