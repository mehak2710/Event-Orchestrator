from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    webhook_secret: str = "change-me"
    admin_token: str = ""

    n8n_webhook_url: str = "http://localhost:5678/webhook/events"
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str = ""  # optional: enables execution history on the dashboard
    n8n_timeout_seconds: float = 10.0

    # Slack: n8n reads SLACK_WEBHOOK_URL itself; the worker only uses it as a fallback
    # for dead-letter alerts when n8n is unreachable.
    slack_webhook_url: str = ""
    n8n_alert_url: str = ""  # n8n webhook that turns a dead-letter event into a Slack alert

    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"

    max_attempts: int = 5
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 30.0

    idempotency_ttl_seconds: int = 86400
    processing_lock_seconds: int = 300

    stream_key: str = "events:stream"
    dlq_key: str = "events:dlq"
    consumer_group: str = "workers"
    stream_maxlen: int = 10000

    inbound_stream_key: str = "events:inbound"
    inbound_group: str = "listeners"

    db_path: str = "data/audit.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()