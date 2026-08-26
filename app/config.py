"""VoiceHub AI Gateway — 配置模块."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """环境变量配置（字段 = .env 变量名，大小写不敏感）。"""

    # VoiceHub 主仓库
    voicehub_api_base_url: str = ""
    voicehub_api_key: str = ""

    # LLM（OpenAI 兼容）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: float = 5.0
    llm_max_tokens: int = 512

    # L3 搜索
    tavily_api_key: str = ""

    # 轮询
    poll_interval_seconds: int = 30
    poll_batch_size: int = 20
    # 轮询场景（逗号分隔；song/language 需主仓 Phase 3 状态机支持后再开）
    review_scenes: str = "register,note"
    # REVIEW 冷却（秒）：冷却期内同一待审项不重审，防无限调用 LLM
    review_cooldown_seconds: int = 300

    # 数据库（审核日志）
    database_url: str = "sqlite:///./data/ai_review.db"
    # 管理台独立库（默认与审核日志同库；切 PostgreSQL 时分别配置即可）
    gateway_database_url: str = ""

    # 管理台初始化（首启）
    admin_init_user: str = ""
    admin_init_pass: str = ""

    # 加密根密钥（Fernet + HMAC 共享；未配置时供应商 Key 仅允许 env 注入）
    # 生成：openssl rand -hex 32 → 转 base64 后填入；32 字节 → base64 长度 44
    admin_secret: str = ""

    # 日志
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()