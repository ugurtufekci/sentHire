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
    # Offline labeling oracle (senthire.evals.autolabel). Never on the request
    # path, so it is free to be slower and stronger than the screening tiers.
    label_oracle_model: str = "claude-sonnet-5"
    # ANTHROPIC_API_KEY is read from the environment by the Anthropic SDK itself.

    # --- intake limits (docs/02 Stage 0) ---
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 25

    # --- batch (economy) transport: LLM tokens bill at 50% (docs/07 §5) ---
    # First poll is short because small batches often finish in seconds; later
    # polls back off to a steady interval. Total wait is capped so a stuck batch
    # fails the run instead of polling forever (the API's own ceiling is 24h).
    batch_poll_initial_seconds: int = 20
    batch_poll_interval_seconds: int = 60
    batch_max_wait_seconds: int = 24 * 3600
    batch_discount: float = 0.5

    # --- screening funnel knobs (docs/02 Stage 5 selection policy) ---
    shortlist_top_k: int = 10
    deep_band_extra: int = 10  # decision band = ranks 1..(top_k + extra)
    deep_confidence_threshold: float = 0.7
    deep_weight_threshold: float = 0.10  # req weight share that makes low confidence matter

    # --- auth & sessions ---
    session_cookie_name: str = "senthire_session"
    session_ttl_days: int = 30
    invitation_ttl_days: int = 7
    password_reset_ttl_minutes: int = 60
    # Cap on outstanding (unused, unexpired) reset tokens per account, so the
    # forgot-password form cannot be used to bomb an inbox.
    password_reset_max_active: int = 3
    # Set true behind HTTPS so the session cookie is marked Secure.
    secure_cookies: bool = False
    # Used to build invitation links shown to admins.
    app_base_url: str = "http://localhost:3000"

    # --- outbound email (invitations, password resets) ---
    # "console" logs emails to stdout (default, zero-config dev);
    # "smtp" delivers via the SMTP settings below (Mailpit in docker-compose,
    # any transactional provider in production).
    email_backend: str = "console"
    email_from: str = "sentHire <no-reply@senthire.app>"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False

    # --- billing (CV-volume pricing, iyzico for the Turkish market) ---
    # "mock" activates plans instantly without payment (local dev / demos);
    # "iyzico" runs the real checkout — requires the iyzico_* settings below.
    billing_provider: str = "mock"
    iyzico_api_key: str | None = None
    iyzico_secret_key: str | None = None
    # Sandbox by default; switch to https://api.iyzipay.com for production.
    iyzico_base_url: str = "https://sandbox-api.iyzipay.com"
    # Map of our plan ids -> iyzico pricing-plan reference codes. Plans are
    # created once in the iyzico dashboard (or via their API) and referenced
    # here, e.g. {"baslangic": "abc-123", "profesyonel": "def-456"}.
    iyzico_plan_refs: dict[str, str] = {}
    # Shared-secret path segment for the payment webhook; unset disables it.
    billing_webhook_token: str | None = None

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
