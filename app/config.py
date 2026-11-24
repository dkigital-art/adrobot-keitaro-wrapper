from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    keitaro_url: str = "https://keitaro-for-tests.etolegko.com/admin_api/v1"
    keitaro_api_key: str = ""

    keitaro_domain_id: int | None = 1813
    keitaro_group_id: int | None = 10332
    keitaro_traffic_source_id: int | None = 836
    keitaro_domain_name: str = ""
    keitaro_group_name: str = "FORTESTS"
    keitaro_traffic_source_name: str = "FORTESTS"

    database_url: str = "sqlite:///./adrobot.db"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    @field_validator("database_url")
    @classmethod
    def _anchor_sqlite_path(cls, value: str) -> str:
        # Относительный путь SQLite считаем от папки проекта, а не от текущего каталога запуска.
        prefix = "sqlite:///"
        if not value.startswith(prefix):
            return value
        path = value[len(prefix):]
        if not path or path.startswith(":memory:") or Path(path).is_absolute():
            return value
        return prefix + (PROJECT_ROOT / path).resolve().as_posix()


@lru_cache
def get_settings() -> Settings:
    return Settings()
