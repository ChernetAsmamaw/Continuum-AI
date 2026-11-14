"""Application configuration, loaded from environment / .env."""

from __future__ import annotations

import logging
import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

log = logging.getLogger("continuum.config")

# Public, checked-in defaults. Fine on a laptop; never acceptable anywhere else.
DEFAULT_AUTH_SECRET = "continuum-dev-secret-change-me"
DEFAULT_DEMO_PASSWORD = "continuum"
DEV_ENVIRONMENTS = {"development", "dev", "local", "test"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT_DIR / ".env"), str(ROOT_DIR / "backend" / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Continuum"
    environment: str = "development"
    demo_mode: bool = True
    auto_seed: bool = True

    # MongoDB
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "continuum"
    continuum_db_mode: str = "atlas"  # atlas | memory
    mongodb_vector_index: str = "memories_vector"

    # LLM: any LangChain provider. openai | gemini (Google AI Studio key) |
    # anthropic | ollama | groq | mistralai | …
    # LLM_MODEL empty = a sensible default for the provider (see agent/llm.py).
    llm_provider: str = "openai"
    llm_model: str = ""
    # Model for the console assistant (tool calling). Empty: a capable default for the provider.
    assistant_model: str = ""
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    # auto | local | none | any LangChain embeddings provider (openai,
    # gemini, voyageai, cohere, ollama). "auto" tries the
    # provider matching LLM_PROVIDER, then falls back to the built-in local model.
    embedding_provider: str = "auto"
    # Optional separate endpoint/key for embeddings (defaults to the LLM's).
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    # Built-in local model (fastembed, runs on CPU, no key). 384 dimensions.
    local_embedding_model: str = "BAAI/bge-small-en-v1.5"
    # vLLM-served reasoning models (e.g. Qwen3.x) answer in a "reasoning" field
    # by default, leaving message.content null. Disabling thinking makes the
    # model answer directly in content.
    llm_disable_thinking: bool = False

    # LangSmith (LANGSMITH_* names work too). Tracing turns on when a key is set
    # unless LANGCHAIN_TRACING_V2/LANGSMITH_TRACING is explicitly false.
    langchain_tracing_v2: bool | None = None
    langchain_api_key: str | None = None
    langchain_project: str = "continuum-hackathon"
    langsmith_api_key: str | None = None
    langsmith_project: str | None = None
    langsmith_tracing: bool | None = None

    # ElevenLabs
    elevenlabs_api_key: str | None = None
    elevenlabs_agent_id: str | None = None
    continuum_voice_secret: str | None = None
    elevenlabs_webhook_secret: str | None = None
    elevenlabs_phone_number: str | None = None
    # ElevenLabs phone-number id (Agents → Phone numbers) used for outbound calls.
    elevenlabs_phone_number_id: str | None = None

    # Shared secret for clinic/lab systems posting FHIR to /api/integrations/fhir
    # (header X-Continuum-Integration-Token). Coordinators can also post signed in.
    integration_secret: str | None = None

    # EHR ingestion (docs/ingestion.md). A FHIR R4 base URL turns on discharge
    # pulls (triggered by HL7 ADT^A03 or on demand) and the result poller.
    ehr_fhir_base_url: str | None = None
    ehr_auth_mode: str = "none"  # none | bearer | backend_jwt
    ehr_bearer_token: str | None = None
    ehr_client_id: str | None = None
    ehr_token_url: str | None = None
    ehr_private_key_path: str | None = None  # PEM for backend_jwt (RS384)
    ehr_key_id: str | None = None
    ehr_scopes: str = (
        "system/Patient.read system/Encounter.read system/ServiceRequest.read "
        "system/Appointment.read system/MedicationRequest.read system/DiagnosticReport.read "
        "system/DocumentReference.read system/Binary.read"
    )
    ehr_poll_enabled: bool = True
    # Optional MLLP listener for HL7v2 feeds from an interface engine. 0 = off.
    hl7_mllp_port: int = 0
    hl7_mllp_host: str = "127.0.0.1"

    # Proactive scheduler. 0 disables the background loop (the sweep can still
    # be run on demand from POST /api/admin/sweep).
    scheduler_interval_seconds: int = 300
    overdue_checkin_after_days: int = 1
    overdue_escalate_after_days: int = 7
    stale_report_after_days: int = 5
    # Automated calls are only placed inside this local-time window (start
    # inclusive, end exclusive) in CALL_TIMEZONE. A check-in that comes due
    # outside it waits for the next window instead of ringing at midnight.
    call_window_start_hour: int = 9
    call_window_end_hour: int = 20
    call_timezone: str = "America/Toronto"

    # Outbound messaging. SMS goes through Twilio's REST API; email through
    # any SMTP relay. Used to page the on-call clinician and, with consent,
    # to text or email patients who chose those channels.
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    # On-call clinician for urgent and emergency escalations (comma-separated
    # for more than one). Paged by SMS and email at once.
    on_call_phone: str | None = None
    on_call_email: str | None = None
    # Re-page when nobody acknowledges within this many minutes, up to the cap.
    page_ack_minutes_emergency: int = 10
    page_ack_minutes_urgent: int = 30
    page_max_attempts: int = 3
    # Country calling code assumed for numbers saved without one ("416 555
    # 0142"). Only "1" (North America: 10-digit national numbers) can be
    # completed safely; for any other value, numbers must be saved as +<cc>….
    default_country_code: str = "1"

    # Auth (demo coordinator sign-in). Override AUTH_SECRET in production.
    auth_secret: str = DEFAULT_AUTH_SECRET
    auth_token_ttl_minutes: int = 600
    demo_coordinator_email: str = "coordinator@continuum.demo"
    demo_coordinator_password: str = DEFAULT_DEMO_PASSWORD
    demo_coordinator_name: str = "Jordan Davis"
    demo_clinician_email: str = "clinician@continuum.demo"
    demo_clinician_password: str = DEFAULT_DEMO_PASSWORD
    demo_clinician_name: str = "Dr. Amara Osei"

    # PHI retention: how long operational records (events, evidence, voice
    # transcripts, audit entries, memories) are kept before becoming eligible
    # for purge. 0 disables the retention sweep entirely.
    phi_retention_days: int = 365

    # App
    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )
    public_app_url: str = "http://localhost:5173"
    demo_anchor_date: str | None = None

    @property
    def is_development(self) -> bool:
        return self.environment.strip().lower() in DEV_ENVIRONMENTS

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def effective_llm_key(self) -> str | None:
        return self.llm_api_key or os.getenv("OPENAI_API_KEY")

    @property
    def llm_enabled(self) -> bool:
        from .agent.llm import chat_configured  # lazy: llm imports settings

        return chat_configured()

    @property
    def tracing_key(self) -> str | None:
        return self.langsmith_api_key or self.langchain_api_key

    @property
    def tracing_project(self) -> str:
        return self.langsmith_project or self.langchain_project

    @property
    def tracing_enabled(self) -> bool:
        flag = self.langsmith_tracing if self.langsmith_tracing is not None else self.langchain_tracing_v2
        return bool(self.tracing_key) and flag is not False

    @property
    def is_memory_db(self) -> bool:
        return self.continuum_db_mode.lower() in {"memory", "mock", "inmemory"}

    @property
    def langsmith_trace_url(self) -> str | None:
        if not self.tracing_enabled:
            return None
        return f"https://smith.langchain.com/o/me/projects/p/{self.tracing_project}"


def harden(s: Settings) -> list[str]:
    """Refuse to run outside development on the public default secrets.

    Returns the warnings it raised (also logged at ERROR so they are loud).
    - Default AUTH_SECRET outside development: anyone who has read this repo
      could mint a coordinator token. Replace it with a random per-process
      secret (sessions end on restart, but tokens can't be forged) and warn.
    - Default demo passwords outside demo mode: the seeded accounts would be
      reachable with a password printed in the README. Demo users are not
      created in that case (see ``auth.ensure_demo_user``); warn as well.
    """
    warnings: list[str] = []
    if not s.is_development and s.auth_secret == DEFAULT_AUTH_SECRET:
        s.auth_secret = secrets.token_urlsafe(48)
        warnings.append(
            f"AUTH_SECRET is the public default while ENVIRONMENT={s.environment!r}. "
            "Using a random per-process secret instead; every restart signs users out. "
            "Set AUTH_SECRET to a strong random value."
        )
    elif s.auth_secret == DEFAULT_AUTH_SECRET:
        log.warning("AUTH_SECRET is the public development default. Do not expose this server.")
    if not s.is_development and len(s.auth_secret) < 32:
        warnings.append("AUTH_SECRET is shorter than 32 characters; use a long random value.")
    if not s.demo_mode and DEFAULT_DEMO_PASSWORD in {s.demo_coordinator_password, s.demo_clinician_password}:
        warnings.append(
            "DEMO_MODE is off but a demo account still uses the default password; "
            "those accounts will not be created. Set DEMO_*_PASSWORD or provision real users."
        )
    if s.demo_mode and not s.is_development:
        warnings.append(f"DEMO_MODE is on while ENVIRONMENT={s.environment!r}: demo endpoints and accounts are exposed.")
    for message in warnings:
        log.error("SECURITY: %s", message)
    return warnings


def export_tracing_env(s: Settings) -> None:
    """LangChain reads tracing config from os.environ, not from .env files."""
    if s.tracing_enabled:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", s.tracing_key or "")
        os.environ.setdefault("LANGSMITH_PROJECT", s.tracing_project)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGCHAIN_TRACING_V2"] = "false"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    harden(s)
    export_tracing_env(s)
    return s


settings = get_settings()
