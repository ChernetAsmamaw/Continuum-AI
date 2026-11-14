"""Transitional Care Management (TCM) clock for each care episode.

Medicare TCM (CPT 99495 / 99496, CMS MLN908628) is the deadline most
primary-care practices actually work to after a discharge:

* an interactive contact with the patient or caregiver within **2 business
  days** of discharge (day 0 = the discharge date). If it fails, two or more
  separate, documented attempts within that window still satisfy it;
* a face-to-face visit within **7 days** (99496, high complexity) or
  **14 days** (99495, moderate complexity);
* medication reconciliation no later than the date of that visit.

Continuum picks the visit window from the patient's risk (High → 7 days,
otherwise 14) unless a clinician sets it per patient. Discharges to a
facility (skilled nursing, hospice, another hospital) or a death are not
TCM-eligible and get no clock.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from pymongo.database import Database

from . import repository as repo
from .util import new_id, parse_date, utcnow

CONTACT_BUSINESS_DAYS = 2
REQUIRED_ATTEMPTS = 2
# Visit categories that can be the TCM face-to-face visit, best first.
VISIT_CATEGORIES = ("family_physician", "follow_up", "coordinator_entry")

OUTCOMES = {
    "reached": "Reached the patient",
    "reached_caregiver": "Reached the caregiver",
    "voicemail": "Left a voicemail",
    "no_answer": "No answer",
    "wrong_number": "Wrong number",
    "declined": "Patient declined",
    "message_sent": "Message sent",
}
# Interactive contact: a two-way conversation with the patient or caregiver.
INTERACTIVE = {"reached", "reached_caregiver"}

_INELIGIBLE = ("skilled", "snf", "nursing facility", "hospice", "expired", "died", "death", "deceased",
               "transfer", "acute care", "inpatient rehab", "long term care", "long-term care")


def add_business_days(start: date, days: int) -> date:
    """``days`` business days after ``start`` (Saturday and Sunday skipped)."""
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def visit_window_days(patient: dict) -> int:
    override = (patient.get("tcm") or {}).get("visit_window_days")
    if override in (7, 14):
        return override
    return 7 if (patient.get("risk") or "").lower() == "high" else 14


# TCM covers the 30 days that start on the discharge date.
SERVICE_DAYS = 30


def is_eligible(episode: dict | None, today: date | None = None) -> tuple[bool, str | None]:
    if not episode or not parse_date(episode.get("discharge_date")):
        return False, "No discharge date on the active episode."
    if today and (today - parse_date(episode["discharge_date"])).days >= SERVICE_DAYS:
        return False, f"The 30-day transitional care period ended on {(parse_date(episode['discharge_date']) + timedelta(days=SERVICE_DAYS - 1)).isoformat()}."
    disposition = (episode.get("disposition") or "").lower()
    if any(word in disposition for word in _INELIGIBLE):
        return False, f"Discharged to {episode.get('disposition')}: not a community setting, so TCM does not apply."
    return True, None


# ── contact attempts ─────────────────────────────────────────────────────


def record_contact(
    db: Database,
    *,
    patient_id: str,
    outcome: str,
    channel: str,
    actor: str,
    note: str | None = None,
    ref: str | None = None,
    at: str | None = None,
    direction: str = "outbound",
) -> dict:
    """Log one contact attempt. Idempotent on ``ref`` (a call or message id)."""
    if outcome not in OUTCOMES:
        raise ValueError(f"Unknown outcome {outcome!r}")
    if ref:
        existing = db.contact_attempts.find_one({"patient_id": patient_id, "ref": ref}, {"_id": 0})
        if existing:
            if existing["outcome"] != outcome and outcome in INTERACTIVE:
                db.contact_attempts.update_one({"id": existing["id"]}, {"$set": {"outcome": outcome}})
                existing["outcome"] = outcome
            return existing
    from .db import demo_today

    episode = repo.get_active_episode(db, patient_id) or {}
    doc = {
        "id": new_id("con"),
        # The calendar day on the app's clock (demo mode shifts it), which is
        # what the TCM window is measured in.
        "day": (parse_date(at) or demo_today()).isoformat(),
        "patient_id": patient_id,
        "episode_id": episode.get("id"),
        "at": at or utcnow(),
        "channel": channel,
        "direction": direction,
        "outcome": outcome,
        "interactive": outcome in INTERACTIVE,
        "note": note,
        "ref": ref,
        "actor": actor,
    }
    db.contact_attempts.insert_one(dict(doc))
    repo.audit(
        db,
        patient_id=patient_id,
        actor=actor,
        action="Logged contact attempt",
        detail=f"{channel}: {OUTCOMES[outcome]}" + (f" · {note}" if note else ""),
        after={"contact_id": doc["id"], "outcome": outcome},
    )
    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "episode_id": doc["episode_id"],
            "type": "coordinator" if direction == "outbound" else "voice_call",
            "title": f"Contact attempt · {OUTCOMES[outcome]}",
            "detail": f"{channel}" + (f" · {note}" if note else ""),
            "actor": actor,
        },
    )
    return doc


def list_contacts(db: Database, patient_id: str, episode_id: str | None = None) -> list[dict]:
    query: dict[str, Any] = {"patient_id": patient_id}
    if episode_id:
        query["episode_id"] = episode_id
    return list(db.contact_attempts.find(query, {"_id": 0}).sort([("day", 1), ("at", 1)]))


# ── the clock ────────────────────────────────────────────────────────────


def _visit_obligation(obligations: list[dict], episode_id: str | None, visit_due: date) -> dict | None:
    """The follow-up that serves as the TCM visit: a flagged one, else the
    primary-care visit due closest to (and preferably within) the window."""
    pool = [o for o in obligations if not episode_id or o.get("episode_id") in (None, episode_id)]
    flagged = [o for o in pool if o.get("tcm_visit")]
    if flagged:
        return flagged[0]
    for category in VISIT_CATEGORIES:
        hits = [o for o in pool if o.get("category") == category and o.get("status") != "cancelled"]
        if hits:
            hits.sort(key=lambda o: (abs(((parse_date(o.get("due_date")) or visit_due) - visit_due).days), o.get("due_date") or ""))
            return hits[0]
    return None


def clock(db: Database, patient: dict, today: date, obligations: list[dict] | None = None) -> dict:
    """The TCM status of the patient's active episode, for the console."""
    episode = repo.get_active_episode(db, patient["id"])
    eligible, reason = is_eligible(episode, today)
    if not eligible:
        return {"eligible": False, "reason": reason}
    discharge = parse_date(episode["discharge_date"])
    contact_due = add_business_days(discharge, CONTACT_BUSINESS_DAYS)
    window = visit_window_days(patient)
    visit_due = discharge + timedelta(days=window)
    contacts = list_contacts(db, patient["id"], episode.get("id"))
    def day_of(c: dict) -> date:
        return parse_date(c.get("day")) or parse_date(c.get("at")) or today

    in_window = [c for c in contacts if day_of(c) <= contact_due]
    interactive = next((c for c in contacts if c.get("interactive")), None)
    attempts_in_window = len([c for c in in_window if c["outcome"] != "message_sent"])

    if interactive and day_of(interactive) <= contact_due:
        contact_status = "met"
    elif attempts_in_window >= REQUIRED_ATTEMPTS:
        contact_status = "attempts_documented"
    elif today > contact_due:
        contact_status = "missed"
    elif today == contact_due:
        contact_status = "due_today"
    else:
        contact_status = "open"

    if obligations is None:
        obligations = repo.list_obligations(db, patient["id"], today=today)
    visit = _visit_obligation(obligations, episode.get("id"), visit_due)
    visit_date = parse_date((visit or {}).get("confirmed_date") or (visit or {}).get("completed_date"))
    if visit and visit.get("status") == "verified":
        visit_status = "met"
    elif visit and visit.get("status") == "scheduled" and visit_date and visit_date <= visit_due:
        visit_status = "booked"
    elif visit and visit.get("status") == "scheduled" and visit_date:
        visit_status = "booked_late"
    elif today > visit_due:
        visit_status = "missed"
    else:
        visit_status = "not_booked"

    return {
        "eligible": True,
        "episode_id": episode.get("id"),
        "discharge_date": discharge.isoformat(),
        "contact_due": contact_due.isoformat(),
        "contact_status": contact_status,
        "contact_attempts": attempts_in_window,
        "contact_required_attempts": REQUIRED_ATTEMPTS,
        "contact_made_at": interactive["at"] if interactive else None,
        "visit_window_days": window,
        "visit_window_source": "clinician" if (patient.get("tcm") or {}).get("visit_window_days") else "risk",
        "visit_due": visit_due.isoformat(),
        "visit_status": visit_status,
        "visit_obligation_id": (visit or {}).get("id"),
        "visit_date": visit_date.isoformat() if visit_date else None,
        "med_rec_due": visit_due.isoformat(),
        "code": "99496" if window == 7 else "99495",
        "days_since_discharge": (today - discharge).days,
        "contacts": contacts[-10:],
    }


def set_visit_window(db: Database, patient_id: str, days: int | None, *, actor: str) -> dict | None:
    patient = repo.get_patient(db, patient_id)
    if not patient:
        return None
    before = (patient.get("tcm") or {}).get("visit_window_days")
    tcm = dict(patient.get("tcm") or {})
    if days is None:
        tcm.pop("visit_window_days", None)
    else:
        tcm["visit_window_days"] = days
        tcm["set_by"] = actor
    db.patients.update_one({"id": patient_id}, {"$set": {"tcm": tcm}})
    repo.audit(
        db,
        patient_id=patient_id,
        actor=actor,
        action="Set TCM visit window",
        detail=f"{days} days" if days else "Back to risk-based default",
        before={"visit_window_days": before},
        after={"visit_window_days": days},
    )
    return repo.get_patient(db, patient_id)


def schedule_first_contact(db: Database, patient_id: str, *, actor: str, today: date) -> dict | None:
    """Book the post-discharge check-in on the next business day.

    Called at intake and at EHR ingestion. Skipped when the episode is not
    TCM-eligible or a contact check-in already exists for it.
    """
    episode = repo.get_active_episode(db, patient_id)
    eligible, _ = is_eligible(episode)
    if not eligible:
        return None
    if db.checkins.find_one({"patient_id": patient_id, "kind": "tcm_contact", "episode_id": episode.get("id")}):
        return None
    discharge = parse_date(episode["discharge_date"])
    target = max(add_business_days(discharge, 1), today)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    checkin = repo.schedule_checkin(
        db,
        {
            "patient_id": patient_id,
            "episode_id": episode.get("id"),
            "kind": "tcm_contact",
            "scheduled_for": target.isoformat(),
            "reason": (
                "Post-discharge check-in: how they are doing, medications, and whether the follow-up "
                f"visit is booked. TCM contact due {add_business_days(discharge, CONTACT_BUSINESS_DAYS).isoformat()}."
            ),
            "created_by": actor,
        },
    )
    repo.audit(
        db,
        patient_id=patient_id,
        actor=actor,
        action="Scheduled first post-discharge contact",
        detail=f"Check-in on {target.isoformat()}",
        after={"checkin_id": checkin["id"]},
    )
    return checkin


MED_REC = "medication_reconciliation"


def ensure_med_rec(db: Database, patient_id: str, *, actor: str, today: date) -> dict | None:
    """Every TCM episode carries one medication reconciliation, owned by a
    clinician and due no later than the face-to-face visit."""
    episode = repo.get_active_episode(db, patient_id)
    eligible, _ = is_eligible(episode)
    patient = repo.get_patient(db, patient_id)
    if not eligible or not patient:
        return None
    due = (parse_date(episode["discharge_date"]) + timedelta(days=visit_window_days(patient))).isoformat()
    existing = db.obligations.find_one(
        {"patient_id": patient_id, "episode_id": episode.get("id"), "category": MED_REC}, {"_id": 0}
    )
    if existing:
        if existing.get("due_date") != due and existing.get("status") not in ("verified", "cancelled"):
            db.obligations.update_one({"id": existing["id"]}, {"$set": {"due_date": due}})
        return existing
    return repo.create_obligation(
        db,
        {
            "patient_id": patient_id,
            "episode_id": episode.get("id"),
            "description": "Medication reconciliation",
            "category": MED_REC,
            "owner": "Clinician",
            "due_date": due,
            "status": "pending",
            "keywords": ["medication", "medications", "reconciliation", "meds"],
            "verification_required": True,
            "source": {"event_id": None, "label": "TCM requirement", "quote": "Medication reconciliation by the face-to-face visit"},
            "latest_evidence": "",
        },
        actor=actor,
        reason="TCM: medication reconciliation due by the face-to-face visit",
    )


def start_episode(db: Database, patient_id: str, *, actor: str, today: date) -> None:
    """Everything a new TCM-eligible discharge needs on day 0."""
    schedule_first_contact(db, patient_id, actor=actor, today=today)
    ensure_med_rec(db, patient_id, actor=actor, today=today)


def worklist(db: Database, today: date) -> dict:
    """Patients grouped by the TCM deadline they need next."""
    lanes: dict[str, list[dict]] = {"contact_due": [], "contact_missed": [], "visit_unbooked": [], "visit_missed": []}
    week_end = today + timedelta(days=7)
    for patient in db.patients.find({}, {"_id": 0}):
        state = clock(db, patient, today)
        if not state.get("eligible"):
            continue
        row = {
            "patient_id": patient["id"],
            "name": patient.get("name"),
            "risk": patient.get("risk"),
            "discharge_date": state["discharge_date"],
            "contact_due": state["contact_due"],
            "contact_attempts": state["contact_attempts"],
            "visit_due": state["visit_due"],
            "code": state["code"],
        }
        if state["contact_status"] in ("open", "due_today"):
            lanes["contact_due"].append(row)
        elif state["contact_status"] == "missed":
            lanes["contact_missed"].append(row)
        if state["visit_status"] == "missed":
            lanes["visit_missed"].append(row)
        elif state["visit_status"] in ("not_booked", "booked_late") and parse_date(state["visit_due"]) <= week_end:
            lanes["visit_unbooked"].append(row)
    for rows in lanes.values():
        rows.sort(key=lambda r: (r["contact_due"], r["visit_due"]))
    return {"today": today.isoformat(), "lanes": lanes}
