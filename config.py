import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Process Extractor Pro"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    # ENCRYPTION_KEY: 32-byte key encoded as base64.
    # Generate with: python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
    # Leave empty in dev — a stable dev key is auto-derived (DO NOT use in production).
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CUSTOM_AI_BASE_URL: str = os.getenv("CUSTOM_AI_BASE_URL", "")
    CUSTOM_AI_API_KEY: str = os.getenv("CUSTOM_AI_API_KEY", "")
    CUSTOM_AI_MODEL: str = os.getenv("CUSTOM_AI_MODEL", "claude-sonnet-4-6")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/app.db")
    DATA_DIR: str = os.path.join(os.path.dirname(__file__), "data")
    SCREENSHOTS_DIR: str = os.path.join(DATA_DIR, "screenshots")
    RECORDINGS_DIR: str = os.path.join(DATA_DIR, "recordings")
    REPORTS_DIR: str = os.path.join(DATA_DIR, "reports")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # Stripe — https://dashboard.stripe.com/apikeys
    STRIPE_SECRET_KEY: str = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY: str = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    # Stripe webhook signing secret — https://dashboard.stripe.com/webhooks
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    # Public base URL used for Stripe redirect URLs
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000")

    class Config:
        env_file = ".env"


settings = Settings()
