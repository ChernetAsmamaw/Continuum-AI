"""Care episodes over time: readmission and closing an episode.

One patient, many hospital stays. Each stay is an episode with its own TCM
clock. A readmission opens a new episode; follow-ups still open from the last
one are carried over (marked, never silently dropped) so the team can decide
what still applies. Closing an episode refuses while loops are open unless the
person closing it states why those loops end.
"""

from __future__ import annotations

from datetime import date

from pymongo.database import Database

from . import repository as repo
from .schemas import OPEN_STATUSES, ObligationStatus
from .util import new_id, parse_date, utcnow

CLOSE_REASONS = {"window_complete", "readmitted", "transferred", "died", "declined_follow_up", "other"}

_OPEN = {s.value for s in OPEN_STATUSES}


class OpenLoopsError(Exception):
    def __init__(self, open_items: list[dict]):
        super().__init__("Episode has open follow-ups")
        self.open_items = open_items


def list_episodes(db: Database, patient_id: str) -> list[dict]:
    return list(db.care_episodes.find({"patient_id": patient_id}, {"_id": 0}).sort("discharge_date", -1))


def _open_items(db: Database, patient_id: str, episode_id: str | None) -> list[dict]:
    query = {"patient_id": patient_id, "status": {"$in": sorted(_OPEN)}}
    if episode_id:
        query["episode_id"] = {"$in": [episode_id, None]}
    return list(db.obligations.find(query, {"_id": 0}))


def open_readmission(
    db: Database,
    patient_id: str,
    *,
    admission_date: str | None,
    discharge_date: str,
    reason: str | None,
    disposition: str | None,
    actor: str,
    today: date,
) -> dict:
    """Close the active episode as 'readmitted' and open the new stay."""
    previous = repo.get_active_episode(db, patient_id)
    discharge = parse_date(discharge_date)
    if not discharge:
        raise ValueError("discharge_date must be YYYY-MM-DD")
    now = utcnow()
    if previous:
        db.care_episodes.update_one(
            {"id": previous["id"]},
            {"$set": {"status": "closed", "closed_reason": "readmitted", "closed_at": now, "closed_by": actor}},
        )
    episode = {
        "id": new_id("ep"),
        "patient_id": patient_id,
        "admission_date": (parse_date(admission_date) or discharge).isoformat(),
        "discharge_date": discharge.isoformat(),
        "reason": reason or "Readmission",
        "disposition": disposition,
        "status": "active",
        "readmission_of": previous["id"] if previous else None,
        "discharge_summary": "",
        "created_at": now,
    }
    db.care_episodes.insert_one(dict(episode))

    carried = []
    for item in _open_items(db, patient_id, previous["id"] if previous else None):
        db.obligations.update_one(
            {"id": item["id"]},
            {
                "$set": {"episode_id": episode["id"], "carried_over_from": item.get("episode_id"),
                         "next_action": "Carried over from the previous stay: confirm it still applies"},
                "$push": {"history": {"at": now, "status": item["status"], "actor": actor,
                                      "reason": "Carried over to the readmission episode"}},
            },
        )
        carried.append(item["id"])
    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "episode_id": episode["id"],
            "type": "coordinator",
            "title": "Readmission recorded",
            "detail": f"Discharged {episode['discharge_date']}; {len(carried)} open follow-up(s) carried over for review.",
            "actor": actor,
        },
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor=actor,
        action="Opened readmission episode",
        detail=f"{episode['id']} (previous {previous['id'] if previous else 'none'})",
        before={"episode_id": previous["id"] if previous else None},
        after={"episode_id": episode["id"], "carried_over": carried},
    )
    from . import tcm

    tcm.start_episode(db, patient_id, actor=actor, today=today)
    return {"episode": episode, "carried_over": carried, "previous_episode_id": previous["id"] if previous else None}


def close_episode(db: Database, patient_id: str, *, reason: str, note: str | None, close_open: bool, actor: str) -> dict:
    """Close the active episode. Open loops block it unless ``close_open``,
    which cancels them with the stated reason as the override."""
    if reason not in CLOSE_REASONS:
        raise ValueError(f"reason must be one of {sorted(CLOSE_REASONS)}")
    episode = repo.get_active_episode(db, patient_id)
    if not episode:
        raise LookupError("No active episode")
    open_items = _open_items(db, patient_id, episode["id"])
    if open_items and not close_open:
        raise OpenLoopsError(open_items)
    explanation = f"Episode closed ({reason.replace('_', ' ')})" + (f": {note}" if note else "")
    for item in open_items:
        repo.add_evidence(
            db,
            patient_id=patient_id,
            obligation_id=item["id"],
            kind="coordinator_override",
            summary=explanation,
            source_ref=None,
            actor=actor,
        )
        repo.set_obligation_status(db, item, ObligationStatus.cancelled.value, actor, explanation, thread_id=patient_id)
    db.care_episodes.update_one(
        {"id": episode["id"]},
        {"$set": {"status": "closed", "closed_reason": reason, "closed_note": note, "closed_at": utcnow(), "closed_by": actor}},
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor=actor,
        action="Closed care episode",
        detail=explanation,
        before={"episode_id": episode["id"], "status": "active"},
        after={"status": "closed", "cancelled_open": [i["id"] for i in open_items]},
    )
    repo.add_event(
        db,
        {"patient_id": patient_id, "episode_id": episode["id"], "type": "coordinator", "title": "Care episode closed",
         "detail": explanation, "actor": actor},
    )
    return {"episode_id": episode["id"], "cancelled": [i["id"] for i in open_items]}
