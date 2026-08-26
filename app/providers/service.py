"""VoiceHub AI Gateway — 供应商服务（Fernet 加密 API Key + 热生效）。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..db import GwProvider
from ..security import fernet_decrypt, fernet_encrypt
from .registry import PROVIDER_TEMPLATES


def list_providers(session: Session) -> list[GwProvider]:
    return session.query(GwProvider).order_by(GwProvider.priority.asc(), GwProvider.id.asc()).all()


def get_default_provider(session: Session) -> Optional[GwProvider]:
    """默认供应商：priority 最小 + enabled。"""
    return (
        session.query(GwProvider)
        .filter(GwProvider.enabled.is_(True))
        .order_by(GwProvider.priority.asc(), GwProvider.id.asc())
        .first()
    )


def decrypt_key(encrypted: Optional[str]) -> str:
    if not encrypted:
        return ""
    try:
        return fernet_decrypt(encrypted)
    except Exception:
        return ""


def create_provider(
    session: Session,
    *,
    name: str,
    base_url: str,
    model: str,
    api_key: str = "",
    timeout_seconds: float = 5.0,
    max_tokens: int = 512,
    enabled: bool = True,
    priority: int = 100,
    note: str = "",
    is_builtin: bool = False,
) -> GwProvider:
    encrypted = fernet_encrypt(api_key) if api_key else None
    p = GwProvider(
        name=name,
        base_url=base_url,
        model=model,
        api_key_encrypted=encrypted,
        timeout_seconds=timeout_seconds,
        max_tokens=max_tokens,
        enabled=enabled,
        priority=priority,
        is_builtin=is_builtin,
        note=note,
    )
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def update_provider(session: Session, provider_id: int, **fields) -> GwProvider | None:
    p = session.query(GwProvider).filter(GwProvider.id == provider_id).one_or_none()
    if not p:
        return None
    if "api_key" in fields:
        fields["api_key_encrypted"] = fernet_encrypt(fields.pop("api_key")) if fields["api_key"] else None
    for k, v in fields.items():
        setattr(p, k, v)
    session.commit()
    return p


def delete_provider(session: Session, provider_id: int) -> bool:
    p = session.query(GwProvider).filter(GwProvider.id == provider_id).one_or_none()
    if not p or p.is_builtin:
        return False
    session.delete(p)
    session.commit()
    return True


def seed_default_providers(session: Session) -> None:
    """首启：若表为空，预置模板。"""
    if session.query(GwProvider).count() > 0:
        return
    priority = 10
    for tpl in PROVIDER_TEMPLATES:
        create_provider(
            session,
            name=tpl["name"],
            base_url=tpl["base_url"],
            model=tpl["model"],
            timeout_seconds=60.0 if "Ollama" in tpl["name"] else 5.0,
            max_tokens=512,
            enabled=False,  # 默认未启用，校方填 Key 后启用
            priority=priority,
            note=tpl["note"],
            is_builtin=True,
        )
        priority += 10