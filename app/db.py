"""VoiceHub AI Gateway — 审核日志存储（SQLite 默认，兼容 PostgreSQL）。"""
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

Base = declarative_base()


class AiReviewLog(Base):
    """AI 审核日志：对象/场景/模型/decision/置信度/耗时/数据源。"""

    __tablename__ = "ai_review_logs"

    id = sa.Column(sa.Integer, primary_key=True)
    scene = sa.Column(sa.String(32), nullable=False)      # register/song/note/language
    target_id = sa.Column(sa.Integer, nullable=False)     # 主仓库对象 ID
    decision = sa.Column(sa.String(16), nullable=False)   # APPROVE/REJECT/REVIEW
    reason = sa.Column(sa.Text, nullable=True)
    confidence = sa.Column(sa.Float, nullable=True)
    model = sa.Column(sa.String(64), nullable=True)       # LLM 模型名（或 L1/L3）
    source = sa.Column(sa.String(32), nullable=True)      # l1_rules/l2_llm/l3_search
    duration_ms = sa.Column(sa.Integer, nullable=True)
    payload_json = sa.Column(sa.Text, nullable=True)      # 送审原文快照
    created_at = sa.Column(sa.DateTime, server_default=sa.func.now(), nullable=False)


engine = create_engine(settings.database_url, connect_args={"check_same_thread": False} if settings.database_url.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """建表（幂等）。"""
    Base.metadata.create_all(bind=engine)