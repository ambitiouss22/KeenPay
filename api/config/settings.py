"""Pydantic settings — load from environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    # Binding all interfaces is required inside a container; the process is
    # reached only through the reverse proxy, never exposed directly.
    api_host: str = "0.0.0.0"  # noqa: S104  # nosec B104
    api_port: int = 8000

    database_url: str = "postgresql+asyncpg://keenpay:keenpay@localhost:5432/keenpay"
    redis_url: str = "redis://localhost:6379/0"

    cors_origins: str = "http://localhost:3000"
    frontend_url: str = "http://localhost:3000"

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 60
    jwt_refresh_expire_days: int = 7

    openai_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 10

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""
    razorpay_mock: bool = True

    merchant_policy_json: str = "./api/config/merchant_policy.json"

    enable_dev_routes: bool = True
    enable_trace_streaming: bool = True
    enable_metrics: bool = True

    rate_limit_rpm: int = 120
    worker_concurrency: int = 2

    use_in_memory_store: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
