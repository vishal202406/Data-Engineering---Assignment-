"""Central configuration, loaded from environment variables / .env."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_db: str = "marketplace"
    mysql_user: str = "pipeline"
    mysql_password: str = "change_me"

    input_dir: str = "./archive"
    internal_dir: str = "./data/internal"
    raw_dir: str = "./data/raw"
    cache_dir: str = "./data/cache"

    api_timeout_seconds: float = 15.0
    api_max_attempts: int = 3

    @property
    def sqlalchemy_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def input_path(self) -> Path:
        return Path(self.input_dir)

    @property
    def internal_path(self) -> Path:
        return Path(self.internal_dir)

    @property
    def raw_path(self) -> Path:
        return Path(self.raw_dir)

    @property
    def cache_path(self) -> Path:
        return Path(self.cache_dir)


def get_settings() -> Settings:
    return Settings()
