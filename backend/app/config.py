from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Tender AI"
    app_environment: str = "development"
    database_url: str = "sqlite:///./data/tender_ai.db"
    upload_dir: Path = Path("uploads")
    company_evidence_dir: Path = Path("company_evidence")
    export_dir: Path = Path("exports")
    max_upload_size_mb: int = 25
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout_seconds: float = 30.0
    index_dir: Path = Path("data/indexes")
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    ai_provider: str = "ollama"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    auth_required: bool = False
    auth_session_days: int = 14
    dev_user_email: str = "local-dev@example.invalid"
    auth_secret: str = "development-only-change-me"
    session_cookie_name: str = "tender_ai_session"
    allowed_origins: str = ""
    allowed_hosts: str = ""
    log_level: str = "info"
    redis_url: str = ""
    job_max_retries: int = 3
    job_timeout: str = "30m"
    rate_limit_enabled: bool = False
    rate_limit_login: int = 10
    rate_limit_ai: int = 30
    rate_limit_upload: int = 20
    rate_limit_window_seconds: int = 60
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_starter: str = ""
    stripe_price_professional: str = ""
    stripe_price_business: str = ""
    stripe_success_url: str = "http://127.0.0.1:8000/?billing=success"
    stripe_cancel_url: str = "http://127.0.0.1:8000/?billing=cancel"
    stripe_portal_return_url: str = "http://127.0.0.1:8000/"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
# Fail-safe: production deployments must not use the development placeholder
# secret, must have AUTH_REQUIRED enabled, must have ALLOWED_ORIGINS configured,
# and must declare ALLOWED_HOSTS.  Local development keeps its escape hatches.
if settings.app_environment.lower() == "production":
    if not settings.auth_required:
        raise RuntimeError("AUTH_REQUIRED must be true in production.")
    if settings.auth_secret == "development-only-change-me":
        raise RuntimeError("AUTH_SECRET must be set to a non-placeholder value in production.")
    if not settings.allowed_origins:
        raise RuntimeError("ALLOWED_ORIGINS must be set in production.")
    if not settings.allowed_hosts:
        raise RuntimeError("ALLOWED_HOSTS must be set in production.")
if settings.storage_backend.lower() == "s3" and not settings.s3_bucket:
    raise RuntimeError("S3_BUCKET must be set when STORAGE_BACKEND=s3.")
if settings.ai_provider.lower() == "openai" and not settings.openai_api_key:
    raise RuntimeError("OPENAI_API_KEY must be set when AI_PROVIDER=openai.")
if settings.auth_required and settings.auth_secret == "development-only-change-me":
    raise RuntimeError("AUTH_SECRET must be changed when AUTH_REQUIRED is enabled.")
if settings.app_environment.lower() == "production" and not settings.auth_required:
    raise RuntimeError("AUTH_REQUIRED must be true in production.")
settings.upload_dir.mkdir(parents=True, exist_ok=True)
settings.company_evidence_dir.mkdir(parents=True, exist_ok=True)
settings.export_dir.mkdir(parents=True, exist_ok=True)
Path("data").mkdir(parents=True, exist_ok=True)
