"""PHI retention: report on, and (when applied) purge, aged operational records.

The sweep covers the high-churn operational collections that accumulate PHI
(event logs, evidence, voice transcripts, agent audit entries, memories and
check-in schedules). Core record-keeping collections — patients, episodes,
obligations and their history — are never touched: they are the clinic's
permanent coordination record.

The default operation is a dry-run report. ``apply_retention`` only deletes
records older than ``PHI_RETENTION_DAYS`` and is intended to be invoked
deliberately by a coordinator (see the role-gated admin endpoint).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from .config import settings
from .db import get_db

log = logging.getLogger("continuum.retention")

# Collection -> the ISO timestamp field used for age.
PHI_COLLECTIONS: dict[str, str] = {
    "events": "created_at",
    "evidence": "recorded_at",
    "voice_calls": "created_at",
    "agent_audit": "created_at",
    "memories": "created_at",
    "checkins": "created_at",
    "hl7_messages": "received_at",
    "ingest_runs": "at",
    "ingest_jobs": "created_at",  # holds the pulled EHR record
    "actions": "intended_at",
    "call_sessions": "created_at",  # transcripts
}


def cutoff_iso(retention_days: int) -> str:
    cutoff = date.today() - timedelta(days=retention_days)
    return f"{cutoff.isoformat()}T00:00:00+00:00"


def retention_report() -> dict:
    """Dry-run: count, per collection, the records eligible for purge."""
    retention_days = settings.phi_retention_days
    if retention_days <= 0:
        return {
            "retention_days": retention_days,
            "cutoff": None,
            "eligible": {},
            "total": 0,
            "note": "Retention sweep is disabled (PHI_RETENTION_DAYS <= 0).",
        }
    cutoff = cutoff_iso(retention_days)
    db = get_db()
    eligible: dict[str, int] = {}
    for collection, field in PHI_COLLECTIONS.items():
        eligible[collection] = db[collection].count_documents({field: {"$lt": cutoff}})
    return {
        "retention_days": retention_days,
        "cutoff": cutoff,
        "eligible": eligible,
        "total": sum(eligible.values()),
        "note": (
            "Dry-run report only — no records were deleted. Applying requires a "
            "coordinator to POST /api/admin/retention/apply."
        ),
    }


def apply_retention(actor: str = "unknown") -> dict:
    """Delete eligible records from the operational collections."""
    retention_days = settings.phi_retention_days
    if retention_days <= 0:
        return {
            "applied": False,
            "reason": "Retention sweep is disabled (PHI_RETENTION_DAYS <= 0).",
            "deleted": {},
            "total": 0,
        }
    cutoff = cutoff_iso(retention_days)
    db = get_db()
    deleted: dict[str, int] = {}
    for collection, field in PHI_COLLECTIONS.items():
        result = db[collection].delete_many({field: {"$lt": cutoff}})
        deleted[collection] = result.deleted_count
    total = sum(deleted.values())
    log.info("Retention sweep by %s: deleted %d records older than %s", actor, total, cutoff)
    return {
        "applied": True,
        "retention_days": retention_days,
        "cutoff": cutoff,
        "deleted": deleted,
        "total": total,
        "note": "Patients, episodes, obligations and approvals were not touched.",
    }
