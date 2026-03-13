"""Application configuration loaded from environment variables."""

import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """App settings from environment."""

    gemini_api_key: str = ""
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/visabot"
    sync_database_url: str = "postgresql://postgres:postgres@localhost:5432/visabot"
    gemini_model: str = "gemini-2.0-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    embedding_dimension: int = 3072
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
