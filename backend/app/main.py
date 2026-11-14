"""Continuum FastAPI application."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import bootstrap

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("continuum")

# Query-string credentials (the console's EventSource sends its JWT as
# ``/api/stream?token=…``) must never reach a log file.
_SECRET_QUERY = re.compile(r"(?i)([?&](?:token|access_token|ticket|key|secret)=)[^&\s\"]*")


def redact_secrets(text: str) -> str:
    return _SECRET_QUERY.sub(r"\1[REDACTED]", text)


class RedactQuerySecrets(logging.Filter):
    """Strip token-like query values from uvicorn access-log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact_secrets(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        return True


_REDACTOR = RedactQuerySecrets()
for _name in ("uvicorn.access", "uvicorn.error", "uvicorn"):
    _logger = logging.getLogger(_name)
    if not any(isinstance(f, RedactQuerySecrets) for f in _logger.filters):
        _logger.addFilter(_REDACTOR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        bootstrap()
        log.info("Continuum ready (db=%s, llm=%s)", settings.mongodb_db, settings.llm_enabled)
    except Exception as exc:  # pragma: no cover - environment dependent
        log.error("Startup bootstrap failed: %s", exc)
    task = None
    if settings.scheduler_interval_seconds > 0:
        import asyncio

        from .scheduler import scheduler_loop

        task = asyncio.create_task(scheduler_loop(settings.scheduler_interval_seconds))
    try:
        from .streams import start_watcher

        start_watcher()
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("Change stream watcher not started: %s", exc)
    if settings.ehr_fhir_base_url:
        # Finish ingestion jobs a crash left half-done (their leases expire after 90s;
        # this worker restarted, so they're claimable now only if the lease lapsed).
        import threading

        def _resume() -> None:
            import time

            from .db import get_db
            from .ingest.jobs import LEASE, resume_pending

            time.sleep(2)
            done = resume_pending(get_db())
            if not done and get_db().ingest_jobs.count_documents({"status": "running"}):
                time.sleep(LEASE.total_seconds())  # held by a worker elsewhere: wait out its lease
                done = resume_pending(get_db())
            for r in done:
                log.info("Resumed ingestion job %s → %s (from step %s)", r["job_id"], r["status"], r.get("resumed_from"))

        threading.Thread(target=_resume, name="resume-ingest", daemon=True).start()
    mllp_task = None
    if settings.hl7_mllp_port > 0:
        import asyncio

        from .ingest.mllp import serve

        mllp_task = asyncio.create_task(serve(settings.hl7_mllp_host, settings.hl7_mllp_port))
    yield
    if task:
        task.cancel()
    if mllp_task:
        mllp_task.cancel()


app = FastAPI(
    title="Continuum API",
    description=(
        "Persistent-memory care coordination agent for post-discharge follow-ups. "
        "MongoDB Atlas · LangChain + LangGraph · ElevenLabs. Synthetic demo data only."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal error. Check the API logs.",
            "hint": "If MongoDB is unreachable, set MONGODB_URI or CONTINUUM_DB_MODE=memory.",
        },
    )


from .routes import agent, auth, calls as calls_routes, ingest, integrations, live, meta, obligations, patients, voice  # noqa: E402
from .deps import require_auth  # noqa: E402
from fastapi import Depends  # noqa: E402

# All data endpoints live under /api so the React app can own client routes such
# as /patients and /approvals without colliding with the JSON API.
API_PREFIX = "/api"
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(meta.router, prefix=API_PREFIX)
app.include_router(patients.router, prefix=API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(obligations.router, prefix=API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(agent.router, prefix=API_PREFIX, dependencies=[Depends(require_auth)])
# Clinic/lab integrations accept a shared token or a coordinator JWT.
app.include_router(integrations.router, prefix=API_PREFIX)
# EHR ingestion (HL7 trigger, FHIR pull, result poll): same auth as integrations.
app.include_router(ingest.router, prefix=API_PREFIX)
# Change-stream SSE (token in query) and the action ledger.
app.include_router(live.router, prefix=API_PREFIX)
# Call Room: voice call sessions (browser, phone, simulated).
app.include_router(calls_routes.router, prefix=API_PREFIX)
# Voice webhooks authenticate via the shared secret, not the coordinator JWT.
app.include_router(voice.router, prefix=API_PREFIX)
from .routes.review import router as review_router  # noqa: E402  (coordinator review queue)
from .routes import tcm as tcm_routes  # noqa: E402
from .routes import work as work_routes  # noqa: E402
from .routes import portal as portal_routes  # noqa: E402
app.include_router(review_router, prefix=API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(tcm_routes.router, prefix=API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(work_routes.router, prefix=API_PREFIX, dependencies=[Depends(require_auth)])
app.include_router(portal_routes.router, prefix=API_PREFIX)  # public: patient link + Twilio signature


@app.get("/health", include_in_schema=False)
def health_probe() -> dict:
    """Root health probe for hosting platforms; the full payload lives at /api/health."""
    return meta.health()


_DIST = Path(__file__).resolve().parents[2] / "dist"
if _DIST.exists():
    from fastapi.responses import FileResponse

    _ASSETS = _DIST / "assets"
    if _ASSETS.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Serve the Vite single-page app and fall back to index.html for client routes."""
        candidate = _DIST / full_path
        if full_path and candidate.is_file() and _DIST in candidate.parents:
            return FileResponse(candidate)
        # index.html must never be cached: it names the hashed bundle, and a stale
        # copy keeps loading an old build after every deploy.
        return FileResponse(_DIST / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
else:

    @app.get("/")
    def root() -> dict:
        return {
            "app": "Continuum API",
            "docs": "/docs",
            "health": "/health",
            "note": "Build the frontend (npm run build) to serve the UI from /.",
        }
