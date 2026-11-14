"""Action ledger: what Continuum tried to do vs. what actually happened.

Every outward action (an outbound call, a task handed to a coordinator) is a
row in ``actions`` that moves through explicit states:

    intended ──▶ sent ──▶ confirmed          (proof arrived)
        │          ├────▶ failed             (the other system said no)
        │          └────▶ unconfirmed        (sent, but no proof by the deadline)
        └────────────────▶ unknown           (crashed between deciding and hearing back:
                                              we can't tell if it went out, so a human
                                              reconciles instead of blindly retrying)

"Sent" is never treated as "done". Only the expected proof (a post-call
transcript, a coordinator's decision) confirms an action. The ``key`` makes
intent idempotent: deciding the same thing twice after a restart returns the
existing row instead of sending again.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pymongo.database import Database

from . import repository as repo
from .util import new_id, utcnow

log = logging.getLogger("continuum.ledger")

INTENDED, SENT, CONFIRMED, FAILED, UNCONFIRMED, UNKNOWN = (
    "intended", "sent", "confirmed", "failed", "unconfirmed", "unknown",
)
STATUSES = [INTENDED, SENT, CONFIRMED, FAILED, UNCONFIRMED, UNKNOWN]
TERMINAL = {CONFIRMED, FAILED}
# An intent with no send result after this long means the process died mid-send.
STALE_INTENT = timedelta(minutes=10)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def intend(
    db: Database,
    *,
    kind: str,
    patient_id: str,
    summary: str,
    proof: str,
    actor: str,
    key: str | None = None,
    obligation_id: str | None = None,
    confirm_within_hours: float = 48,
    metadata: dict | None = None,
) -> dict:
    """Record the decision to act *before* acting. Idempotent on ``key``."""
    if key:
        existing = db.actions.find_one({"key": key, "status": {"$nin": [FAILED]}}, {"_id": 0})
        if existing:
            return {**existing, "reused": True}
    now = datetime.now(timezone.utc)
    action = {
        "id": new_id("act"),
        "key": key,
        "kind": kind,
        "patient_id": patient_id,
        "obligation_id": obligation_id,
        "summary": summary,
        "proof_expected": proof,
        "actor": actor,
        "status": INTENDED,
        "intended_at": _iso(now),
        "confirm_by": _iso(now + timedelta(hours=confirm_within_hours)),
        "external_ref": None,
        "history": [{"at": _iso(now), "status": INTENDED, "detail": summary}],
        "metadata": metadata or {},
    }
    db.actions.insert_one(dict(action))
    return action


def _move(db: Database, action_id: str, status: str, detail: str, **fields) -> dict | None:
    now = utcnow()
    update = {"$set": {"status": status, f"{status}_at": now, "updated_at": now, **fields}, "$push": {"history": {"at": now, "status": status, "detail": detail}}}
    db.actions.update_one({"id": action_id}, update)
    return db.actions.find_one({"id": action_id}, {"_id": 0})


def mark_sent(db: Database, action_id: str, *, external_ref: str | None = None, detail: str = "Sent") -> dict | None:
    return _move(db, action_id, SENT, detail, external_ref=external_ref)


def mark_unknown(db: Database, action_id: str, detail: str) -> dict | None:
    return _move(db, action_id, UNKNOWN, detail)


def mark_failed(db: Database, action_id: str, detail: str) -> dict | None:
    return _move(db, action_id, FAILED, detail)


def confirm(db: Database, *, external_ref: str, evidence: str, kind: str | None = None) -> dict | None:
    """Proof arrived for whatever action carries ``external_ref``."""
    query: dict = {"external_ref": external_ref, "status": {"$in": [SENT, UNCONFIRMED, UNKNOWN, INTENDED]}}
    if kind:
        query["kind"] = kind
    action = db.actions.find_one(query, {"_id": 0})
    if not action:
        return None
    updated = _move(db, action["id"], CONFIRMED, evidence, confirmation=evidence)
    log.info("Action %s confirmed: %s", action["id"], evidence)
    return updated


def on_approval_resolved(db: Database, approval: dict) -> None:
    """A coordinator task is confirmed by the coordinator's decision."""
    if not approval.get("id"):
        return
    action = db.actions.find_one({"external_ref": approval["id"], "status": {"$nin": list(TERMINAL)}}, {"_id": 0})
    if not action:
        return
    who = approval.get("resolved_by") or "coordinator"
    if approval.get("status") == "approved":
        _move(db, action["id"], CONFIRMED, f"Marked done by {who}", confirmation=f"approval {approval['id']} approved by {who}")
    else:
        _move(db, action["id"], FAILED, f"Declined by {who}" + (f": {approval.get('resolution_note')}" if approval.get("resolution_note") else ""))


def reconcile(db: Database, now: datetime | None = None) -> list[dict]:
    """Flag actions whose outcome we can't vouch for. Run by the scheduler sweep."""
    now = now or datetime.now(timezone.utc)
    flagged: list[dict] = []
    for action in db.actions.find({"status": INTENDED, "intended_at": {"$lt": _iso(now - STALE_INTENT)}}, {"_id": 0}):
        _move(db, action["id"], UNKNOWN, "Interrupted between deciding and hearing back; check the other system before retrying.")
        flagged.append({**action, "status": UNKNOWN})
    for action in db.actions.find({"status": SENT, "confirm_by": {"$lt": _iso(now)}}, {"_id": 0}):
        _move(db, action["id"], UNCONFIRMED, f"No proof by {action['confirm_by'][:16]}: {action.get('proof_expected')}")
        flagged.append({**action, "status": UNCONFIRMED})
    for action in flagged:
        repo.add_event(
            db,
            {
                "patient_id": action["patient_id"],
                "type": "system",
                "title": "Action outcome unverified" if action["status"] == UNCONFIRMED else "Action outcome unknown",
                "detail": f"{action['summary']} — {'sent but never confirmed' if action['status'] == UNCONFIRMED else 'may or may not have gone out'}.",
                "actor": "Action ledger",
                "metadata": {"action_id": action["id"]},
            },
        )
    return flagged


def list_actions(db: Database, *, patient_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
    query: dict = {}
    if patient_id:
        query["patient_id"] = patient_id
    if status:
        query["status"] = {"$in": status.split(",")}
    return list(db.actions.find(query, {"_id": 0}).sort("intended_at", -1).limit(limit))


def summary(db: Database) -> dict:
    counts = {s: db.actions.count_documents({"status": s}) for s in STATUSES}
    return {"counts": counts, "needs_attention": counts[UNCONFIRMED] + counts[UNKNOWN]}
