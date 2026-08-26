"""VoiceHub AI Gateway — 数据库与 ORM。

双库设计：
- ai_review.db：审核日志（AiReviewLog），对外契约不变
- gateway.db：管理台与配置数据（GwUser/GwSession/GwAuditLog/GwProvider/GwSetting/...）

SQLAlchemy 连接串兼容 SQLite（默认）/ PostgreSQL（切数据库仅改 DATABASE_URL）。
"""
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

Base = declarative_base()


# ============================================================================
# ai_review.db — 审核日志（既有契约，保留）
# ============================================================================
class AiReviewLog(Base):
    __tablename__ = "ai_review_logs"

    id = sa.Column(sa.Integer, primary_key=True)
    scene = sa.Column(sa.String(32), nullable=False)
    target_id = sa.Column(sa.Integer, nullable=False)
    decision = sa.Column(sa.String(16), nullable=False)
    reason = sa.Column(sa.Text, nullable=True)
    confidence = sa.Column(sa.Float, nullable=True)
    model = sa.Column(sa.String(64), nullable=True)
    source = sa.Column(sa.String(32), nullable=True)
    duration_ms = sa.Column(sa.Integer, nullable=True)
    payload_json = sa.Column(sa.Text, nullable=True)
    created_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)


# ============================================================================
# gateway.db — 管理台与配置
# ============================================================================
class GwUser(Base):
    __tablename__ = "gw_users"

    id = sa.Column(sa.Integer, primary_key=True)
    username = sa.Column(sa.String(64), nullable=False, unique=True)
    password_hash = sa.Column(sa.String(256), nullable=False)  # argon2 encoded
    role = sa.Column(sa.String(16), nullable=False, default="viewer")  # admin/reviewer/viewer
    is_active = sa.Column(sa.Boolean, nullable=False, default=True)
    totp_secret = sa.Column(sa.String(64), nullable=True)  # base32, optional
    must_change_password = sa.Column(sa.Boolean, nullable=False, default=False)
    created_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)
    last_login_at = sa.Column(sa.DateTime, nullable=True)
    failed_logins = sa.Column(sa.Integer, nullable=False, default=0)
    locked_until = sa.Column(sa.DateTime, nullable=True)


class GwSession(Base):
    __tablename__ = "gw_sessions"

    id = sa.Column(sa.String(64), primary_key=True)  # 32B token -> 64 hex chars
    user_id = sa.Column(sa.Integer, sa.ForeignKey("gw_users.id"), nullable=False)
    csrf_token = sa.Column(sa.String(64), nullable=False)
    ip = sa.Column(sa.String(64), nullable=True)
    user_agent = sa.Column(sa.String(256), nullable=True)
    created_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)
    last_active_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)
    expires_at = sa.Column(sa.DateTime, nullable=False)


class GwAuditLog(Base):
    __tablename__ = "gw_audit_logs"

    id = sa.Column(sa.Integer, primary_key=True)
    actor = sa.Column(sa.String(64), nullable=False)  # 用户名或"system"
    action = sa.Column(sa.String(64), nullable=False)  # login/logout/...
    target = sa.Column(sa.String(128), nullable=True)
    before_json = sa.Column(sa.Text, nullable=True)
    after_json = sa.Column(sa.Text, nullable=True)
    ip = sa.Column(sa.String(64), nullable=True)
    created_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)


class GwProvider(Base):
    __tablename__ = "gw_providers"

    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.String(64), nullable=False, unique=True)
    base_url = sa.Column(sa.String(256), nullable=False)
    model = sa.Column(sa.String(128), nullable=False)
    api_key_encrypted = sa.Column(sa.Text, nullable=True)  # Fernet 加密
    timeout_seconds = sa.Column(sa.Float, nullable=False, default=5.0)
    max_tokens = sa.Column(sa.Integer, nullable=False, default=512)
    enabled = sa.Column(sa.Boolean, nullable=False, default=True)
    priority = sa.Column(sa.Integer, nullable=False, default=100)  # 越小越优先
    is_builtin = sa.Column(sa.Boolean, nullable=False, default=False)
    note = sa.Column(sa.String(256), nullable=True)
    created_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)
    updated_at = sa.Column(sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False)


class GwSetting(Base):
    __tablename__ = "gw_settings"

    key = sa.Column(sa.String(64), primary_key=True)
    value = sa.Column(sa.Text, nullable=False)
    updated_at = sa.Column(sa.DateTime, server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False)


class GwRule(Base):
    """L1 规则（DB 优先于 rules/extra_patterns.json；skip_scenes 是 JSON 序列化的 set）"""
    __tablename__ = "gw_rules"

    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.String(64), nullable=False, unique=True)
    pattern = sa.Column(sa.String(512), nullable=False)
    label = sa.Column(sa.String(64), nullable=False)
    skip_scenes_json = sa.Column(sa.Text, nullable=True)  # e.g. '["register"]'
    enabled = sa.Column(sa.Boolean, nullable=False, default=True)
    note = sa.Column(sa.String(256), nullable=True)
    created_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)


class GwRoster(Base):
    """名册：学号以 HMAC-SHA256 存储（不落明文学号）"""
    __tablename__ = "gw_roster"

    id = sa.Column(sa.Integer, primary_key=True)
    student_no_hmac = sa.Column(sa.String(64), nullable=False, unique=True)
    name = sa.Column(sa.String(64), nullable=False)
    grade = sa.Column(sa.String(32), nullable=True)
    class_ = sa.Column("class", sa.String(32), nullable=True)  # 'class' 是关键字
    imported_by = sa.Column(sa.String(64), nullable=True)
    imported_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)


class GwSpotcheckLog(Base):
    __tablename__ = "gw_spotcheck_logs"

    id = sa.Column(sa.Integer, primary_key=True)
    scene = sa.Column(sa.String(32), nullable=False)
    target_id = sa.Column(sa.Integer, nullable=False)
    original_decision = sa.Column(sa.String(16), nullable=False)
    recheck_decision = sa.Column(sa.String(16), nullable=False)
    confidence = sa.Column(sa.Float, nullable=True)
    model = sa.Column(sa.String(64), nullable=True)
    reason = sa.Column(sa.Text, nullable=True)
    reviewed_by = sa.Column(sa.String(64), nullable=True)  # 人工确认标记
    created_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)


# ============================================================================
# 引擎与初始化（双库）
# ============================================================================
def _review_url() -> str:
    return settings.database_url


def _gateway_url() -> str:
    # 兼容 DATABASE_URL（默认）；允许 GATEWAY_DATABASE_URL 覆盖
    return settings.gateway_database_url or settings.database_url


def _engine_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {}


review_engine = create_engine(_review_url(), **_engine_args(_review_url()))
gateway_engine = create_engine(_gateway_url(), **_engine_args(_gateway_url()))

ReviewSession = sessionmaker(bind=review_engine, autoflush=False, expire_on_commit=False)
GatewaySession = sessionmaker(bind=gateway_engine, autoflush=False, expire_on_commit=False)

# 兼容旧名（保持既有 app/workers/poll_pending.py 与 db.py 旧导入）
SessionLocal = ReviewSession


def init_db() -> None:
    """建表（幂等）。两个库分别建。"""
    Base.metadata.create_all(bind=review_engine)
    Base.metadata.create_all(bind=gateway_engine)