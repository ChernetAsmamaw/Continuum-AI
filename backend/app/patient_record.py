"""Keeping a patient record correct after intake.

* ``find_duplicates``: the same person entered twice, checked before a manual
  intake creates a record (EHR ingestion has its own identity review).
* ``update_patient``: an audited edit of demographics, contact, caregiver and
  preferences. History is before/after, never a silent overwrite.
* ``age_from_birth_date``: one source of truth for age once a DOB is known.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pymongo.database import Database

from . import repository as repo
from .util import parse_date, utcnow


def _norm_name(value: str | None) -> str:
    return " ".join((value or "").lower().replace(".", " ").replace(",", " ").split())


def _digits(value: str | None) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def age_from_birth_date(birth_date: str | None, today: date) -> int | None:
    born = parse_date(birth_date)
    if not born:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def find_duplicates(
    db: Database,
    *,
    name: str,
    birth_date: str | None = None,
    phone: str | None = None,
    mrn: str | None = None,
    exclude_id: str | None = None,
) -> list[dict]:
    """Existing patients who are probably the same person.

    A match is any of: the same MRN; the same name and birth date; the same
    name and phone number. A shared name alone is not enough (common names
    collide), and a different birth date rules a name match out.
    """
    want_name = _norm_name(name)
    want_dob = (parse_date(birth_date) or None) and parse_date(birth_date).isoformat()
    want_phone = _digits(phone)
    want_mrn = (mrn or "").strip().upper()

    out: list[dict] = []
    fields = {"_id": 0, "id": 1, "name": 1, "birth_date": 1, "phone": 1, "mrn": 1, "age": 1, "condition": 1}
    for patient in db.patients.find({}, fields):
        if patient["id"] == exclude_id:
            continue
        reasons = []
        if want_mrn and (patient.get("mrn") or "").upper() == want_mrn:
            reasons.append("same MRN")
        same_name = want_name and _norm_name(patient.get("name")) == want_name
        their_dob = patient.get("birth_date")
        if same_name and want_dob and their_dob == want_dob:
            reasons.append("same name and date of birth")
        their_phone = _digits(patient.get("phone"))
        dob_conflict = bool(want_dob and their_dob and their_dob != want_dob)
        if same_name and want_phone and their_phone and their_phone[-10:] == want_phone[-10:] and not dob_conflict:
            reasons.append("same name and phone number")
        if reasons:
            out.append({**patient, "match_reasons": reasons})
    return out[:5]


# Fields a person may edit, grouped by where they live on the document.
TOP_LEVEL = {"name", "birth_date", "mrn", "condition", "risk", "pronouns", "phone", "email"}
PREFERENCE_FIELDS = {
    "channel",
    "best_time",
    "language",
    "interpreter_needed",
    "timezone",
    "consent_to_automated_calls",
    "consent_to_recording",
}
CAREGIVER_FIELDS = {"name", "phone", "relationship", "may_discuss_care"}


def update_patient(db: Database, patient_id: str, changes: dict[str, Any], *, actor: str, today: date) -> dict | None:
    """Apply an edit and write one audit entry with before/after values.

    Returns the updated patient, or ``None`` when the patient does not exist.
    Only fields that actually change are written.
    """
    patient = repo.get_patient(db, patient_id)
    if not patient:
        return None

    prefs = dict(patient.get("preferences") or {})
    caregiver = dict(patient.get("caregiver_contact") or {})
    if not caregiver and patient.get("caregiver"):
        caregiver = {"name": patient["caregiver"]}

    update: dict[str, Any] = {}
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    for key in TOP_LEVEL:
        if key in changes and changes[key] is not None and changes[key] != patient.get(key):
            value = changes[key]
            if key == "birth_date":
                parsed = parse_date(value)
                value = parsed.isoformat() if parsed else None
            update[key] = value
            before[key], after[key] = patient.get(key), value

    pref_changes = changes.get("preferences") or {}
    for key in PREFERENCE_FIELDS:
        if key in pref_changes and pref_changes[key] is not None and pref_changes[key] != prefs.get(key):
            before[f"preferences.{key}"], after[f"preferences.{key}"] = prefs.get(key), pref_changes[key]
            prefs[key] = pref_changes[key]
            update["preferences"] = prefs

    cg_changes = changes.get("caregiver") or {}
    for key in CAREGIVER_FIELDS:
        if key in cg_changes and cg_changes[key] is not None and cg_changes[key] != caregiver.get(key):
            before[f"caregiver.{key}"], after[f"caregiver.{key}"] = caregiver.get(key), cg_changes[key]
            caregiver[key] = cg_changes[key]
            update["caregiver_contact"] = caregiver
            update["caregiver"] = caregiver_label(caregiver)

    if "birth_date" in update:
        update["age"] = age_from_birth_date(update["birth_date"], today)
    if "name" in update:
        first, _, last = update["name"].partition(" ")
        update["initials"] = "".join(ch for ch in first[:1] + last[:1] if ch.isalpha()).upper() or update["name"][:2].upper()

    if not update:
        return patient

    update["updated_at"] = utcnow()
    db.patients.update_one({"id": patient_id}, {"$set": update})
    fields = ", ".join(sorted(after))
    repo.audit(
        db,
        patient_id=patient_id,
        actor=actor,
        action="Updated patient record",
        detail=f"Changed {fields}",
        before=before,
        after=after,
    )
    repo.add_event(
        db,
        {
            "patient_id": patient_id,
            "type": "coordinator",
            "title": "Patient record updated",
            "detail": f"Changed {fields}",
            "actor": actor,
        },
    )
    return repo.get_patient(db, patient_id)


def caregiver_label(caregiver: dict) -> str | None:
    """The one-line caregiver shown in lists: "Name (relationship)"."""
    name = (caregiver.get("name") or "").strip()
    if not name:
        return None
    relationship = (caregiver.get("relationship") or "").strip()
    return f"{name} ({relationship})" if relationship else name


class DuplicatePatientError(Exception):
    """Raised by intake when the patient probably already exists."""

    def __init__(self, candidates: list[dict]):
        super().__init__("Possible duplicate patient")
        self.candidates = candidates
