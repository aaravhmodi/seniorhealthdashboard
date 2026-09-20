from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "dev"
    mock_mode: bool = True

    deepgram_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # -- Linq (caregiver messaging) ---------------------------------------
    linq_api_key: str = ""
    linq_api_base: str = "https://api.linqapp.com/api/partner"
    linq_webhook_secret: str = "devsecret"
    # Which of our Linq lines the care circle is created from. Empty means
    # "ask Linq for the first healthy number on the account" (see linq.py).
    linq_from_number: str = ""
    # The care team's own handle, added to every circle so the family is
    # never texting a robot with nobody behind it.
    linq_care_team_number: str = ""

    duckdb_path: str = "./data/warehouse.duckdb"

    # -- Follow-up and escalation timing ----------------------------------
    # Real cadence is 24h and 72h post-discharge. On stage nobody waits a day,
    # so every interval is divided by this. Set FOLLOWUP_TIME_SCALE=2880 and a
    # 24-hour follow-up lands in 30 seconds.
    followup_time_scale: float = 1.0
    followup_hours: list[float] = [24.0, 72.0]
    # How long a caregiver has to tapback an alert before the next caregiver
    # in the escalation order is texted.
    escalation_timeout_minutes: float = 10.0

    # Where the caregiver link in a Linq text points. No PHI in the text itself.
    public_web_base: str = "http://localhost:5173"
    # Public origin of this API, used when registering the Linq webhook.
    public_api_base: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
