"""运行配置。所有环境变量在此集中声明，其余模块只依赖 Settings，不直接读 os.environ。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM（PRD §13.4）
    anthropic_api_key: str = Field(default="", repr=False)
    llm_model_primary: str = "claude-sonnet-5"
    llm_model_fallback: str = "claude-haiku-4-5"
    llm_model_judge: str = "claude-haiku-4-5"  # eval 的 LLM 评判只做语气与 groundedness

    # 数据库（ADR-0001：单实例双 schema）
    database_url: str = "postgresql+psycopg://cs_agent:cs_agent@localhost:5432/cs_agent"

    # JWT（FR-801）。本阶段无登录流程，token 由 `cs_agent.auth.jwt.issue_token` 签发
    jwt_secret: str = Field(default="", repr=False)
    jwt_issuer: str = "cs-agent"
    jwt_expire_minutes: int = 60

    # Langfuse
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = Field(default="", repr=False)

    app_env: str = "dev"
    log_level: str = "INFO"

    @property
    def llm_configured(self) -> bool:
        return self.anthropic_api_key.startswith("sk-ant-") and len(self.anthropic_api_key) > 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
