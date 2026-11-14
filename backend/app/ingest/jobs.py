"""Resumable ingestion jobs: survive a crash without replaying finished work.

A discharge is processed as a job in ``ingest_jobs`` with ordered steps:

    pull ──▶ identify ──▶ structured ──▶ narrative ──▶ verify

* Each step's output is written to the job document **in the same update**
  that marks the step done, so the job only ever says "done" about work whose
  result is saved. The pulled EHR record itself is stored after ``pull``; a
  resumed job never re-fetches it.
* A worker claims a job with an atomic lease (``find_one_and_update``), so two
  API instances can't run the same job, and a crashed worker's lease simply
  expires.
* Every step is also idempotent on its own (follow-ups keyed by EHR
  reference, narrative extracted once per stay), so even a step interrupted
  half-way is safe to run again.
* ``resume_pending`` runs at startup and in every scheduler sweep.

For the demo, ``INGEST_CRASH_AFTER_STEP=<step>`` (only honoured when
``DEMO_MODE=true``) kills the process right after that step is checkpointed.
"""

from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.database import Database

from ..config import settings
from ..util import new_id, utcnow
from . import builder
from .fhir_client import FHIRClient, FHIRError, client_from_settings
from .packet import DischargePacket
from .pull import PullError, pull_discharge

log = logging.getLogger("continuum.ingest.jobs")

STEPS = ["pull", "identify", "structured", "narrative", "verify"]
LEASE = timedelta(seconds=90)
WORKER = f"{socket.gethostname()}:{os.getpid()}"
MAX_ATTEMPTS = 5


class SimulatedCrash(RuntimeError):
    """Raised by tests in place of killing the process."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def create_job(db: Database, *, key: str, params: dict, actor: str, source: str) -> dict:
    """Create (or return the existing) job for an idempotency key."""
    existing = db.ingest_jobs.find_one({"key": key}, {"_id": 0})
    if existing:
        return existing
    now = utcnow()
    job = {
        "id": new_id("job"),
        "key": key,
        "status": "queued",
        "source": source,
        "actor": actor,
        "params": params,
        "steps": {name: {"status": "pending", "attempts": 0} for name in STEPS},
        "step_order": STEPS,
        "packet": None,
        "state": {},
        "attempts": 0,
        "lease_owner": None,
        "lease_until": None,
        "created_at": now,
        "updated_at": now,
    }
    db.ingest_jobs.insert_one(dict(job))
    return job


def _claim(db: Database, job_id: str, *, force: bool = False) -> dict | None:
    now = _now()
    query: dict = {"id": job_id, "status": {"$in": ["queued", "running", "failed"]}}
    if not force:
        query["$or"] = [{"lease_until": None}, {"lease_until": {"$lt": _iso(now)}}, {"lease_owner": WORKER}]
    return db.ingest_jobs.find_one_and_update(
        query,
        {"$set": {"status": "running", "lease_owner": WORKER, "lease_until": _iso(now + LEASE), "updated_at": utcnow()},
         "$inc": {"attempts": 1}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )


def _crash_point(step: str, crash_after: str | None) -> None:
    target = crash_after or (os.environ.get("INGEST_CRASH_AFTER_STEP") if settings.demo_mode else None)
    if target != step:
        return
    if crash_after:
        raise SimulatedCrash(f"simulated crash after {step}")
    log.warning("INGEST_CRASH_AFTER_STEP=%s: terminating the process now (demo).", step)
    os._exit(70)


def _apply_set(doc: dict, fields: dict) -> None:
    """Mirror a ``$set`` with dotted paths onto the in-memory job document."""
    for path, value in fields.items():
        cur = doc
        parts = path.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value


def _run_step(db: Database, job: dict, step: str, client: FHIRClient | None) -> dict:
    """Execute one step; return the fields to persist with its completion."""
    params, state = job["params"], dict(job.get("state") or {})
    actor = job.get("actor") or builder.ACTOR
    if step == "pull":
        fhir = client or client_from_settings()
        if fhir is None:
            raise FHIRError("EHR_FHIR_BASE_URL is not configured")
        packet = pull_discharge(
            fhir,
            patient_fhir_id=params.get("patient_fhir_id"),
            patient_identifiers=params.get("patient_identifiers") or [],
            encounter_fhir_id=params.get("encounter_fhir_id"),
            encounter_identifiers=params.get("encounter_identifiers") or [],
            source=job["source"],
            source_ref=params.get("source_ref"),
        )
        return {
            "packet": packet.model_dump(),
            "summary": f"{len(packet.orders)} orders, {len(packet.appointments)} appointments, "
            f"{len(packet.pending_results)} pending results, {len(packet.medications)} meds",
        }
    packet = DischargePacket(**job["packet"])
    if step == "identify":
        out = builder.step_identify(db, packet, actor)
        return {"state": {**state, **out}, "summary": f"{out['outcome']} ({out['matched_by']}) → {out['patient_id']}"}
    if step == "structured":
        out = builder.step_structured(db, packet, state, actor)
        return {"state": {**state, **out}, "summary": f"{len(out['created'])} created, {len(out['skipped'])} already tracked"}
    if step == "narrative":
        out = builder.step_narrative(db, packet, state, actor)
        return {"state": {**state, **out}, "summary": f"{len(out['ai_created'])} from narrative ({out['extraction_status']})"}
    if step == "verify":
        result = builder.step_verify(db, packet, state, actor, job_id=job["id"])
        return {"state": state, "result": {k: v for k, v in result.items() if k != "obligations"}, "summary": result["summary"] + " — verified in the database"}
    raise ValueError(step)


def run_job(db: Database, job_id: str, *, client: FHIRClient | None = None, crash_after: str | None = None,
            force: bool = False) -> dict:
    job = _claim(db, job_id, force=force)
    if not job:
        current = db.ingest_jobs.find_one({"id": job_id}, {"_id": 0, "packet": 0}) or {}
        if current.get("status") == "done":
            return {"job_id": job_id, "status": "done", "resumed_from": None, "already_done": True,
                    "steps": current.get("steps"), **(current.get("result") or {})}
        return {"job_id": job_id, "status": current.get("status", "missing"),
                "detail": "Not claimable right now (leased by another worker)", "lease_until": current.get("lease_until")}
    resumed_from = next((s for s in STEPS if job["steps"][s]["status"] != "done"), None)
    if job["attempts"] > 1 and resumed_from:
        log.info("Resuming job %s at step '%s' (attempt %d).", job_id, resumed_from, job["attempts"])
    for step in STEPS:
        if job["steps"][step]["status"] == "done":
            continue
        started = utcnow()
        running = {f"steps.{step}.status": "running", f"steps.{step}.started_at": started, "current_step": step,
                   "lease_until": _iso(_now() + LEASE)}
        db.ingest_jobs.update_one({"id": job_id}, {"$set": running, "$inc": {f"steps.{step}.attempts": 1}})
        _apply_set(job, running)
        job["steps"][step]["attempts"] = job["steps"][step].get("attempts", 0) + 1
        try:
            out = _run_step(db, job, step, client)
        except (PullError, FHIRError, builder.VerificationError) as exc:
            fatal = isinstance(exc, PullError) or job["attempts"] >= MAX_ATTEMPTS
            db.ingest_jobs.update_one(
                {"id": job_id},
                {"$set": {f"steps.{step}.status": "failed", f"steps.{step}.error": str(exc), "error": str(exc),
                          "status": "failed" if fatal else "running", "lease_until": None, "updated_at": utcnow()}},
            )
            log.warning("Job %s failed at %s: %s", job_id, step, exc)
            return {"job_id": job_id, "status": "failed", "failed_step": step, "error": str(exc), "retryable": not fatal}
        update = {f"steps.{step}.status": "done", f"steps.{step}.finished_at": utcnow(), f"steps.{step}.summary": out["summary"],
                  f"steps.{step}.worker": WORKER, "updated_at": utcnow()}
        for field in ("packet", "state", "result"):
            if field in out:
                update[field] = out[field]
        if step == "identify":
            update["patient_id"] = out["state"]["patient_id"]
        db.ingest_jobs.update_one({"id": job_id}, {"$set": update})
        # We hold the lease, so the job is exactly what we just wrote: no re-read.
        _apply_set(job, update)
        _crash_point(step, crash_after)
    finished = {"status": "done", "current_step": None, "lease_owner": None, "lease_until": None, "finished_at": utcnow()}
    db.ingest_jobs.update_one({"id": job_id}, {"$set": finished})
    _apply_set(job, finished)
    return {"job_id": job_id, "status": "done", "resumed_from": resumed_from if job["attempts"] > 1 else None,
            "steps": {s: job["steps"][s] for s in STEPS}, **(job.get("result") or {})}


def _owner_dead(owner: str | None) -> bool:
    """A lease held by a process on this machine that no longer exists."""
    if not owner or ":" not in owner:
        return False
    host, _, pid = owner.rpartition(":")
    if host != socket.gethostname() or not pid.isdigit() or int(pid) == os.getpid():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def resume_pending(db: Database, *, client: FHIRClient | None = None, limit: int = 20) -> list[dict]:
    """Pick up jobs a crash left behind and finish them from their checkpoint.

    Claimable: queued, an expired lease, or a lease whose owner process on this
    machine is gone (so a restart resumes immediately instead of waiting out
    the lease).
    """
    now = _iso(_now())
    out = []
    for job in list(db.ingest_jobs.find({"status": {"$in": ["queued", "running"]}}, {"_id": 0, "id": 1, "lease_until": 1, "lease_owner": 1}).limit(limit)):
        expired = not job.get("lease_until") or job["lease_until"] < now
        dead = _owner_dead(job.get("lease_owner"))
        if expired or dead:
            if dead:
                log.info("Job %s was held by %s, which is no longer running; reclaiming.", job["id"], job["lease_owner"])
            out.append(run_job(db, job["id"], client=client, force=dead))
    return out


def get_job(db: Database, job_id: str) -> dict | None:
    job = db.ingest_jobs.find_one({"id": job_id}, {"_id": 0, "packet": 0})
    return job


def list_jobs(db: Database, limit: int = 20) -> list[dict]:
    return list(db.ingest_jobs.find({}, {"_id": 0, "packet": 0}).sort("created_at", -1).limit(limit))
