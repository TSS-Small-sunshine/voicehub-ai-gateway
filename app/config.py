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

    # 数据库（审核日志）
    database_url: str = "sqlite:///./data/ai_review.db"

    # 日志
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()