from pydantic_settings import BaseSettings
from typing import Literal, Optional


class Settings(BaseSettings):
    # ── Security ────────────────────────────────────────────────────────────
    # Comma-separated list of valid API keys. Leave empty to disable auth (dev).
    api_keys: str = ""
    api_key: str = ""  # single key alias

    # ── Twilio ───────────────────────────────────────────────────────────────
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""  # E.164 format e.g. +15551234567

    # ── Deepgram STT ─────────────────────────────────────────────────────────
    deepgram_api_key: str = ""

    # ── Cartesia TTS ─────────────────────────────────────────────────────────
    cartesia_api_key: str = ""
    cartesia_voice_id_en: str = "a0e99841-438c-4a64-b679-ae501e7d6091"
    cartesia_voice_id_fr: str = "bf991597-6c13-47e4-8411-91ec2de5c466"
    cartesia_model_id: str = "sonic-2"

    # ── Anthropic LLM ────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    llm_fast_model: str = "claude-haiku-4-5-20251001"
    llm_smart_model: str = "claude-sonnet-4-6"

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "sqlite+aiosqlite:///./callcenter.db"

    # ── Redis / Celery ───────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── App ──────────────────────────────────────────────────────────────────
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    base_url: str = "https://your-ngrok-or-domain.com"
    log_level: str = "INFO"
    environment: Literal["development", "production"] = "development"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
