"""Create (or reset) a stage-demo patient with exactly one open follow-up.

A live phone call with a seeded patient walks through four follow-ups; this
patient has one, so the call is short: consent, name and date of birth, one
question, goodbye.

    backend/.venv/bin/python scripts/stage_patient.py +14165550100

The phone number is the one that will ring (use your own). Run again to reset
the patient between rehearsals.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db import demo_today, get_db  # noqa: E402
from app.seed import _insert_obligation, _iso  # noqa: E402
from app.util import utcnow  # noqa: E402

PATIENT_ID = "pat_grace"
EPISODE_ID = "ep_pat_grace"


def main(phone: str) -> None:
    db = get_db()
    today = demo_today()
    for coll in ("obligations", "evidence", "approvals", "checkins", "call_sessions", "events"):
        db[coll].delete_many({"patient_id": PATIENT_ID})
    db.patients.replace_one({"id": PATIENT_ID}, {
        "id": PATIENT_ID,
        "name": "Grace Thompson",
        "initials": "GT",
        "age": 68,
        "birth_date": "1958-05-14",
        "mrn": "C-10466",
        "condition": "Heart failure",
        "risk": "Medium",
        "phone": phone,
        "preferences": {"channel": "Voice call", "best_time": "Afternoons", "language": "English",
                        "consent_to_automated_calls": True, "consent_to_recording": True},
        "synthetic": True,
        "created_at": utcnow(),
    }, upsert=True)
    db.care_episodes.replace_one({"id": EPISODE_ID}, {
        "id": EPISODE_ID, "patient_id": PATIENT_ID, "admission_date": _iso(today - timedelta(days=5)),
        "discharge_date": _iso(today - timedelta(days=2)), "reason": "Heart failure", "status": "active",
        "created_at": utcnow(),
    }, upsert=True)
    _insert_obligation(
        db, patient_id=PATIENT_ID, episode_id=EPISODE_ID,
        description="Bloodwork (potassium)", category="bloodwork", owner="Laboratory",
        due_date=_iso(today + timedelta(days=3)), status="pending",
        next_action="Confirm the blood test is booked",
        quote="Repeat potassium in 5 days after starting spironolactone.",
    )
    digits = "".join(c for c in phone if c.isdigit())
    print(f"Grace Thompson ready: one follow-up (Bloodwork, due {_iso(today + timedelta(days=3))}); "
          f"date of birth May 14, 1958; phone ending {digits[-4:]}.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("+"):
        sys.exit("usage: stage_patient.py +<country><number>")
    main(sys.argv[1])
