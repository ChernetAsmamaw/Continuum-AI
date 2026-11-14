from fastapi import APIRouter, Depends, HTTPException

from .. import repository as repo
from ..agent.llm import chat_model_name, chat_provider, llm_available
from ..config import settings
from ..db import demo_today, get_db, ping, vector_search_available
from ..deps import require_auth, require_role
from ..retention import apply_retention, retention_report
from ..schemas import DemoDayRequest
from ..seed import seed_all
from ..workflow import run_demo_day

router = APIRouter(tags=["meta"])
PROTECTED = [Depends(require_auth)]

SCENARIO = [
    {
        "day": 0,
        "title": "Discharge summary uploaded",
        "channel": "Document",
        "capability": "LangChain extraction and obligation creation",
        "detail": "Four follow-ups become tracked obligations with deadlines and owners.",
    },
    {
        "day": 3,
        "title": "Michael reports seeing his family physician",
        "channel": "ElevenLabs outbound call",
        "capability": "Cross-session memory and patient-reported evidence",
        "detail": "The loop stays open until the clinic confirms.",
    },
    {
        "day": 7,
        "title": "Coordinator confirms cardiology appointment",
        "channel": "Dashboard",
        "capability": "Evidence-based status update",
        "detail": "Clinic record CS-8841 becomes supporting evidence.",
    },
    {
        "day": 10,
        "title": "Michael gives a different cardiology date",
        "channel": "ElevenLabs inbound call",
        "capability": "Contradiction detection",
        "detail": "Both dates are preserved; resolution requires coordinator approval.",
    },
    {
        "day": 12,
        "title": "Michael asks about changing his medication dose",
        "channel": "ElevenLabs call",
        "capability": "Guardrails and clinician escalation",
        "detail": "The voice agent declines clinical advice and escalates.",
    },
]


def _embedding_label() -> str | None:
    from ..agent.llm import embedding_label, get_embeddings

    return embedding_label() if get_embeddings() is not None else None


@router.get("/health")
def health() -> dict:
    db_ok = ping()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "database": "mongomock" if settings.is_memory_db else ("connected" if db_ok else "unreachable"),
        "llm": llm_available(),
        "vector_search": vector_search_available(),
        "embeddings": _embedding_label(),
        "voice_configured": bool(settings.elevenlabs_api_key and settings.elevenlabs_agent_id),
        "ehr_ingestion": bool(settings.ehr_fhir_base_url),
        "tracing": settings.tracing_enabled,
        "llm_provider": f"{chat_provider()}:{chat_model_name()}" if llm_available() else "none (deterministic fallback)",
        "demo_today": demo_today().isoformat(),
        "environment": settings.environment,
    }


@router.get("/config", dependencies=PROTECTED)
def public_config() -> dict:
    return {
        "app_name": settings.app_name,
        "demo_mode": settings.demo_mode,
        "demo_today": demo_today().isoformat(),
        "voice_enabled": bool(settings.elevenlabs_agent_id),
        "elevenlabs_agent_id": settings.elevenlabs_agent_id,
        "langsmith_url": settings.langsmith_trace_url,
        "llm_enabled": llm_available(),
        "model": f"{chat_provider()}:{chat_model_name()}" if llm_available() else "deterministic fallback",
        "scenario": SCENARIO,
        "disclaimer": (
            "Synthetic demo only. Continuum does not diagnose, prescribe or change treatment. "
            "No real patient data is used."
        ),
    }


@router.get("/dashboard", dependencies=PROTECTED)
def dashboard(user: dict = Depends(require_auth)) -> dict:
    return repo.dashboard(get_db(), demo_today(), audience=user.get("role"))


@router.get("/admin/retention", dependencies=PROTECTED)
def retention_preview(user: dict = Depends(require_role("coordinator"))) -> dict:
    """Dry-run report of PHI records eligible for the retention sweep."""
    return retention_report()


@router.post("/admin/sweep", dependencies=PROTECTED)
def sweep_now(user: dict = Depends(require_role("coordinator"))) -> dict:
    """Run the proactive follow-through sweep now (due check-ins, overdue, stale reports)."""
    from ..scheduler import run_sweep

    return run_sweep()


@router.get("/admin/sweep", dependencies=PROTECTED)
def sweep_status() -> dict:
    from ..scheduler import last_run, telephony_configured

    return {
        "interval_seconds": settings.scheduler_interval_seconds,
        "telephony": telephony_configured(),
        "last_run": last_run(),
    }


@router.post("/admin/retention/apply", dependencies=PROTECTED)
def retention_apply(user: dict = Depends(require_role("coordinator"))) -> dict:
    """Apply the retention sweep (coordinators only)."""
    return apply_retention(actor=f"{user.get('name')} ({user.get('role')})")


@router.get("/demo/scenario", dependencies=PROTECTED)
def scenario() -> dict:
    return {"days": SCENARIO, "patient_id": "pat_michael"}


@router.post("/demo/seed", dependencies=PROTECTED)
def seed(reset: bool = False) -> dict:
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="Demo endpoints are disabled")
    return seed_all(reset=reset)


@router.post("/demo/sweep", dependencies=PROTECTED)
def demo_sweep(days_ahead: int = 0) -> dict:
    """Run the follow-through sweep as if ``days_ahead`` days had passed (demo only)."""
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="Demo endpoints are disabled")
    from datetime import datetime, time, timedelta, timezone

    from ..scheduler import run_sweep

    days_ahead = max(0, min(days_ahead, 120))
    today = demo_today() + timedelta(days=days_ahead)
    now = datetime.combine(today, time(23, 59), tzinfo=timezone.utc) if days_ahead else None
    return run_sweep(today=today, now=now)


@router.post("/demo/day/{day}", dependencies=PROTECTED)
def demo_day(day: int, payload: DemoDayRequest | None = None) -> dict:
    if not settings.demo_mode:
        raise HTTPException(status_code=403, detail="Demo endpoints are disabled")
    if day not in {0, 3, 7, 10, 12}:
        raise HTTPException(status_code=422, detail="day must be 0, 3, 7, 10 or 12")
    return run_demo_day(day, actor=(payload.actor if payload else "Demo runner"))
