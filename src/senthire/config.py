from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SENTHIRE_", extra="ignore")

    env: str = "dev"

    # --- database / queue ---
    database_url: str = "postgresql+psycopg://senthire:senthire@localhost:5432/senthire"
    redis_url: str = "redis://localhost:6379/0"

    # --- web clients ---
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- object storage (S3-compatible; MinIO in dev) ---
    s3_endpoint_url: str | None = "http://localhost:9000"
    # Endpoint browsers can reach for presigned PUT/GET (signature binds the host,
    # so inside docker-compose the internal "minio:9000" endpoint can't be used
    # by the browser). Defaults to s3_endpoint_url when unset.
    s3_public_endpoint_url: str | None = None
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

    # --- screening funnel knobs (docs/02 Stage 5 selection policy) ---
    shortlist_top_k: int = 10
    deep_band_extra: int = 10  # decision band = ranks 1..(top_k + extra)
    deep_confidence_threshold: float = 0.7
    deep_weight_threshold: float = 0.10  # req weight share that makes low confidence matter

    # --- auth & sessions ---
    session_cookie_name: str = "senthire_session"
    session_ttl_days: int = 30
    invitation_ttl_days: int = 7
    # Set true behind HTTPS so the session cookie is marked Secure.
    secure_cookies: bool = False
    # Used to build invitation links shown to admins.
    app_base_url: str = "http://localhost:3000"

    # OPT-IN dev backdoor for curl/scripts: requests with this key act as an
    # auto-provisioned "Dev Org" admin. Unset (None) by default so production
    # deployments are cookie-session only; docker-compose sets it for local dev.
    dev_api_key: str | None = None

    prompt_versions: dict[str, str] = {
        "extract": "extract_v1",
        "compile": "compile_v1",
        "light": "screen_v1",
        "deep": "verify_v1",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
