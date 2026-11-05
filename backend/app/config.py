from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    tinyfish_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    database_url: str = "sqlite:///./opportunityos.db"
    app_env: str = "development"
    frontend_origin: str = "http://localhost:3000"
    resend_api_key: str = ""
    email_from: str = ""
    email_to: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()

