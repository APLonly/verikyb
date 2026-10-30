"""Run time Settings"""

from functools import lru_cache

from pydantic import HttpUrl, PositiveFloat, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="verikyb", extra="ignore")
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


@lru_cache
def get_setting() -> Settings:
    return Settings()
