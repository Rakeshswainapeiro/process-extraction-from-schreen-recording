import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Process Extractor Pro"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./data/app.db")
    DATA_DIR: str = os.path.join(os.path.dirname(__file__), "data")
    SCREENSHOTS_DIR: str = os.path.join(DATA_DIR, "screenshots")
    RECORDINGS_DIR: str = os.path.join(DATA_DIR, "recordings")
    REPORTS_DIR: str = os.path.join(DATA_DIR, "reports")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    class Config:
        env_file = ".env"


settings = Settings()
