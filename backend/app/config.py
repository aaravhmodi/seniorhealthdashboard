from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    mock_mode: bool = True

    deepgram_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    linq_api_key: str = ""
    linq_api_base: str = "https://api.linq.app"
    linq_webhook_secret: str = "devsecret"

    duckdb_path: str = "./data/warehouse.duckdb"

    # Where the caregiver link in a Linq text points. No PHI in the text itself.
    public_web_base: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
