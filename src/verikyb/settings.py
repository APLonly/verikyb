"""Run time Settings"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import HttpUrl, NonNegativeFloat, PositiveFloat, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourceMode(StrEnum):
    REPLAY = "replay"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="verikyb_", extra="ignore")
    api_url: HttpUrl = HttpUrl("http://127.0.0.1:8000")
    api_timeout_seconds: PositiveFloat = 30.0
    api_token: SecretStr | None = None
    web_secret_key: SecretStr | None = None
    web_secure_cookies: bool = True
    companies_house_api_key: SecretStr | None = None
    fca_email: str | None = None
    fca_api_key: SecretStr | None = None
    sec_user_agent: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_public_key: str | None = None
    langfuse_host: HttpUrl = HttpUrl("https://us.cloud.langfuse.com")
    sources: SourceMode = SourceMode.REPLAY
    fixtures_dir: Path = Path("data/fixtures")
    cache_ttl_hours: NonNegativeFloat = 24.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
