"""VoiceHub AI Gateway — 管理台账号与会话工具。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from .db import GwSession, GwUser
from .security import (
    csrf_sign,
    csrf_verify,
    hash_password,
    new_token,
    needs_rehash,
    verify_password,
)

SESSION_LIFETIME = timedelta(hours=12)
IDLE_LIFETIME = timedelta(minutes=30)
LOCK_DURATION = timedelta(minutes=15)
MAX_FAILED = 5


def create_user(session: Session, username: str, password: str, role: str = "viewer", must_change_password: bool = False) -> GwUser:
    user = GwUser(
        username=username,
        password_hash=hash_password(password),
        role=role,
        must_change_password=must_change_password,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def find_user(session: Session, username: str) -> Optional[GwUser]:
    return session.query(GwUser).filter(GwUser.username == username).one_or_none()


def authenticate(session: Session, username: str, password: str, ip: str | None = None) -> GwUser | None:
    """返回用户对象（失败原因由调用方处理：返回 None + reason）。"""
    user = find_user(session, username)
    if not user or not user.is_active:
        return None
    if user.locked_until and user.locked_until > datetime.utcnow():
        return None  # 锁定中
    if not verify_password(password, user.password_hash):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILED:
            from datetime import datetime as _dt
            user.locked_until = _dt.utcnow() + LOCK_DURATION
            user.failed_logins = 0
        session.commit()
        return None
    # 成功
    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = datetime.utcnow()
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    session.commit()
    return user


def create_session(session: Session, user_id: int, ip: str | None, user_agent: str | None) -> tuple[str, str]:
    """返回 (session_token, csrf_token)。"""
    token = new_token(32)
    csrf = new_token(24)
    now = datetime.utcnow()
    s = GwSession(
        id=token,
        user_id=user_id,
        csrf_token=csrf,
        ip=ip,
        user_agent=user_agent,
        created_at=now,
        last_active_at=now,
        expires_at=now + SESSION_LIFETIME,
    )
    session.add(s)
    session.commit()
    return token, csrf


def validate_session(session: Session, token: str) -> tuple[GwUser, GwSession] | None:
    s = session.query(GwSession).filter(GwSession.id == token).one_or_none()
    if not s:
        return None
    now = datetime.utcnow()
    if s.expires_at < now:
        session.delete(s)
        session.commit()
        return None
    if now - s.last_active_at > IDLE_LIFETIME:
        session.delete(s)
        session.commit()
        return None
    user = session.query(GwUser).filter(GwUser.id == s.user_id).one_or_none()
    if not user or not user.is_active:
        return None
    s.last_active_at = now
    session.commit()
    return user, s


def revoke_session(session: Session, token: str) -> None:
    s = session.query(GwSession).filter(GwSession.id == token).one_or_none()
    if s:
        session.delete(s)
        session.commit()


def change_password(session: Session, user: GwUser, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    session.commit()


def csrf_check(session_token_csrf: str, form_csrf: str) -> bool:
    """校验表单提交的 CSRF 与会话中的 csrf 一致（且签名未过期）。"""
    raw = csrf_verify(session_token_csrf)
    if raw is None:
        return False
    return secrets_compare(raw, form_csrf)


def secrets_compare(a: str, b: str) -> bool:
    import secrets
    return secrets.compare_digest(a or "", b or "")