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

    # Embedding / RAG（PRD §11、§13.4；Anthropic 不提供 embedding，向量化走独立 provider）
    openai_api_key: str = Field(default="", repr=False)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536  # 与 policy_chunks / memory_embeddings 的 vector(1536) 一致
    rag_top_k: int = 8
    rag_tau_low: float = 0.30  # 占位，Phase 2 用 golden 标定后回填 ADR-0007
    rag_tau_high: float = 0.60

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

    #: 允许跨域的前端来源，逗号分隔。默认只放开本地 Vite 开发服务器。
    cors_origins: str = "http://localhost:5173"

    app_env: str = "dev"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_configured(self) -> bool:
        return self.anthropic_api_key.startswith("sk-ant-") and len(self.anthropic_api_key) > 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
