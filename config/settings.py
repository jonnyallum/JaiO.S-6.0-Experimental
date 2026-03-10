"""
Global settings — loaded from .env via pydantic-settings.
All agents import `settings` for credentials. Never hardcode.
"""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── AI Providers ─────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(...)
    openai_api_key: str = Field("")

    # ── GitHub ────────────────────────────────────────────────────────────────
    github_token: str = Field(...)

    # ── Shared Brain (Supabase lkwydqtfbdjhxaarelaz) ─────────────────────────
    brain_url: str = Field(...)
    brain_service_role_key: str = Field(...)
    brain_connection_string: str = Field("")
    brain_direct_url: str = Field("")

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str = Field(...)
    telegram_allowed_chat_id: str = Field(...)

    # ── Meta / Social ─────────────────────────────────────────────────────────
    facebook_page_access_token: str = Field("")
    facebook_page_id: str = Field("")
    instagram_business_id: str = Field("")
    meta_system_user_token: str = Field("")

    # ── ElevenLabs ────────────────────────────────────────────────────────────
    elevenlabs_api_key: str = Field("")

    # ── Email ─────────────────────────────────────────────────────────────────
    resend_api_key: str = Field("")

    # ── Search ────────────────────────────────────────────────────────────────
    brave_api_key: str = Field("")

    # ── GCP VM ────────────────────────────────────────────────────────────────
    gcp_vm_external_ip: str = Field("35.230.148.83")
    gcp_vm_user: str = Field("antigravity-ai")   # actual server OS username — infrastructure value


settings = Settings()
