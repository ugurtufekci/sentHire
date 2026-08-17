from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SENTHIRE_", extra="ignore")

    env: str = "dev"

    # --- database / queue ---
    database_url: str = "postgresql+psycopg://senthire:senthire@localhost:5432/senthire"
    redis_url: str = "redis://localhost:6379/0"

    # --- object storage (S3-compatible; MinIO in dev) ---
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_bucket: str = "senthire-dev"
    s3_access_key: str = "senthire"
    s3_secret_key: str = "senthire-secret"
    presign_expiry_seconds: int = 900

    # --- model tiers (docs/07 §1) ---
    extraction_model: str = "claude-haiku-4-5"
    extraction_escalation_model: str = "claude-sonnet-5"
    light_screen_model: str = "claude-haiku-4-5"
    deep_analysis_model: str = "claude-sonnet-5"
    compiler_model: str = "claude-sonnet-5"
    # ANTHROPIC_API_KEY is read from the environment by the Anthropic SDK itself.

    # --- intake limits (docs/02 Stage 0) ---
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 25

    # DEV ONLY placeholder auth: requests with this key act as the auto-created dev
    # org. Replaced by real signup/session auth in the auth milestone.
    dev_api_key: str = "dev-local-key"

    prompt_versions: dict[str, str] = {"extract": "extract_v1"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
