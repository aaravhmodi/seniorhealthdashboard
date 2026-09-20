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
    # Destination for the dashboard's explicit "send to my caregiver" action.
    # This may be an iMessage email or an E.164 number; it is kept separate
    # from the caregiver profile phone because demo profiles often contain a
    # plain local number that Linq cannot accept.
    linq_default_recipient: str = ""

    # Retry posture. Linq's own guidance: honour Retry-After on a 429, else
    # exponential backoff, and keep a hard upper bound rather than hammering
    # the wall. A 429 "almost always means a bug", so the ceiling is low on
    # purpose -- if we are hitting it, something is looping.
    linq_timeout_s: float = 12.0
    linq_max_attempts: int = 3
    linq_backoff_base_s: float = 1.0
    # Longer than this we do not hold a request open for; the outbound queue
    # picks it up instead, so a rate limit never blocks a check-in.
    linq_max_wait_s: float = 8.0

    # -- Access control ---------------------------------------------------
    # Signs the caregiver link. Unset means links cannot be minted at all,
    # which the API reports as a 503 rather than falling back to a known key.
    caregiver_link_secret: str = ""
    link_ttl_days: float = 7.0
    # Supabase project JWT secret (Settings > API > JWT Settings). Set this and
    # every patient-data endpoint requires a signed-in user.
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    # A bearer for the Postman collection and the smoke script, which have no
    # Supabase session. Works alongside the JWT, not instead of it.
    api_shared_secret: str = ""

    # -- Supabase (durable state) -----------------------------------------
    # Unset means in-memory only, which is exactly how this ran before. The
    # service-role key bypasses RLS and is backend-only: it must never be
    # given to the browser, which keeps using the publishable key for auth.
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_timeout_s: float = 10.0
    supabase_flush_seconds: float = 3.0

    duckdb_path: str = "./data/warehouse.duckdb"

    # -- Follow-up and escalation timing ----------------------------------
    # Real cadence is 24h and 72h post-discharge. On stage nobody waits a day,
    # so every interval is divided by this. Set FOLLOWUP_TIME_SCALE=2880 and a
    # 24-hour follow-up lands in 30 seconds.
    followup_time_scale: float = 1.0
    # One follow-up, at 24 hours, which is the one that carries the teach-back
    # and the one worth watching. A 72-hour check was here too; it was cut
    # because it bought nothing the 24-hour check does not, and it depended on
    # state surviving three days in a process that does not. Set
    # FOLLOWUP_HOURS='[24, 72]' to bring it back -- nothing else needs changing.
    followup_hours: list[float] = [24.0]
    # How long a caregiver has to tapback an alert before the next caregiver
    # in the escalation order is texted.
    escalation_timeout_minutes: float = 10.0
    # An alert that failed to send is retried on this cadence (multiplied by
    # the attempt number) before it is marked undeliverable and shown red.
    alert_retry_seconds: float = 20.0
    alert_max_delivery_attempts: int = 4

    # Where the caregiver link in a Linq text points. No PHI in the text itself.
    public_web_base: str = "http://localhost:5173"
    # Public origin of this API, used when registering the Linq webhook.
    public_api_base: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
