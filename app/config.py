from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "splanly"
    ENV: str = "development"
    TESTING: bool = False

    DATABASE_URL: str = ""

    SECRET_KEY: str = "change-me-in-production"
    ALLOWED_ORIGINS: str = "*"

    SESSION_COOKIE_NAME: str = "splanly_session"
    SESSION_MAX_AGE_SECONDS: int = 86400  # 24 hours

    # MS365 OAuth (Entra ID)
    MS365_TENANT_ID: str = ""
    MS365_CLIENT_ID: str = ""
    MS365_CLIENT_SECRET: str = ""
    MS365_REDIRECT_URI: str = "http://localhost:8000/auth/callback"

    # Email (SMTP)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "no-reply@skilledca.in"
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    SMTP_INVITATION_FROM_EMAIL: str = "invitation@skilledca.in"
    EMAIL_WORKER_INTERVAL_SECONDS: int = 30
    EMAIL_MAX_RETRIES: int = 3

    # Business defaults
    DEFAULT_BENCH_ROLLOFF_DAYS: int = 7
    BULK_UPLOAD_MAX_ROWS: int = 8000
    CSV_MAX_FILE_SIZE_MB: int = 10

    # Licensing
    LICENSE_SIGNING_KEY: str = "change-me-to-a-strong-random-key"

    # Dev Portal
    DEV_PORTAL_ACCESS_CODE: str = "change-me-dev-portal-code"
    DEV_PORTAL_SESSION_MAX_AGE: int = 7200  # 2 hours
    DEV_PORTAL_ALLOWED_IPS: str = ""  # comma-separated, empty = no restriction

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
