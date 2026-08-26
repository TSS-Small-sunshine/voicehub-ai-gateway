"""VoiceHub AI Gateway — 安全工具（密码/会话 token/对称加密/HMAC/CSRF/TOTP）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import settings

_hasher = PasswordHasher()


# -------- 密码（argon2） --------
def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, encoded: str) -> bool:
    try:
        return _hasher.verify(encoded, plain)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def needs_rehash(encoded: str) -> bool:
    try:
        return _hasher.check_needs_rehash(encoded)
    except Exception:
        return True


# -------- Fernet（API Key 加密存储） --------
def _fernet_key() -> bytes | None:
    """从 ADMIN_SECRET 派生 32B Fernet key（base64）。"""
    raw = (settings.admin_secret or "").strip()
    if not raw:
        return None
    try:
        # 允许 hex(64) 或 raw(32B) 两种形式
        if len(raw) == 64 and all(c in "0123456789abcdefABCDEF" for c in raw):
            key = bytes.fromhex(raw)
        else:
            key = raw.encode("utf-8")[:32].ljust(32, b"\0")
        return base64.urlsafe_b64encode(key)
    except Exception:
        return None


def fernet_encrypt(plaintext: str) -> str:
    """加密；ADMIN_SECRET 未配置则抛 RuntimeError（明文不入库）。"""
    from cryptography.fernet import Fernet
    key = _fernet_key()
    if not key:
        raise RuntimeError("ADMIN_SECRET 未配置，无法加密 API Key；请使用环境变量注入或配置管理台主密钥")
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def fernet_decrypt(token: str) -> str:
    from cryptography.fernet import Fernet, InvalidToken
    key = _fernet_key()
    if not key:
        raise RuntimeError("ADMIN_SECRET 未配置，无法解密")
    try:
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("解密失败：密钥不匹配或数据损坏") from e


# -------- 学号 HMAC（不可逆身份标识） --------
def hmac_student_no(student_no: str) -> str:
    """学号 HMAC-SHA256 十六进制（64 字符）。ADMIN_SECRET 未配置则抛 RuntimeError。"""
    raw = (settings.admin_secret or "").strip()
    if not raw:
        raise RuntimeError("ADMIN_SECRET 未配置，无法计算学号 HMAC")
    return hmac.new(raw.encode("utf-8"), student_no.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def verify_admin_secret() -> bool:
    """运行时检查 ADMIN_SECRET 是否可用（用于路由级守卫）。"""
    return bool((settings.admin_secret or "").strip())


# -------- 会话 token / CSRF --------
def new_token(nbytes: int = 32) -> str:
    """生成密码学随机 token（hex）。"""
    return secrets.token_hex(nbytes)


def csrf_sign(value: str) -> str:
    """CSRF token 用 itsdangerous 签名（防伪造）；未配置密钥则退化为 raw。"""
    try:
        from itsdangerous import URLSafeSerializer
        secret = (settings.admin_secret or "fallback-csrf-secret-do-not-use-in-prod").encode("utf-8")
        return URLSafeSerializer(secret, salt="csrf").dumps(value)
    except Exception:
        return value


def csrf_verify(signed: str, max_age_seconds: int = 12 * 3600) -> str | None:
    try:
        from itsdangerous import URLSafeSerializer, BadSignature, SignatureExpired
        secret = (settings.admin_secret or "fallback-csrf-secret-do-not-use-in-prod").encode("utf-8")
        return URLSafeSerializer(secret, salt="csrf").loads(signed, max_age=max_age_seconds)
    except Exception:
        return None


# -------- TOTP（可选 2FA） --------
def new_totp_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    import pyotp
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def totp_provisioning_uri(secret: str, account: str, issuer: str = "VoiceHub-AI-Gateway") -> str:
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)