"""Create (or reset) Grace Thompson, the stage-demo patient.

The story: Grace was discharged two weeks ago and nobody has reached her, so
her post-discharge call is overdue and sits at the top of "Needs you". The
coordinator clicks Call Grace; on the call she asks to move her family
physician visit, and the reschedule request comes back to "Needs you" for a
person to approve. She has one follow-up, so the call is short.

    backend/.venv/bin/python scripts/stage_patient.py +14165550100

The phone number is the one that will ring (use your own). Run again to reset
the patient between rehearsals.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import tcm  # noqa: E402
from app.db import demo_today, get_db  # noqa: E402
from app.scheduler import _create_task_approval  # noqa: E402
from app.seed import _insert_obligation, _iso  # noqa: E402
from app.util import utcnow  # noqa: E402

PATIENT_ID = "pat_grace"
EPISODE_ID = "ep_pat_grace"


def main(phone: str) -> None:
    db = get_db()
    today = demo_today()
    for coll in ("obligations", "evidence", "approvals", "checkins", "call_sessions", "events", "contact_attempts", "actions"):
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
    discharge = today - timedelta(days=16)  # contact window closed ~12 days ago: top of "Needs you"
    db.care_episodes.replace_one({"id": EPISODE_ID}, {
        "id": EPISODE_ID, "patient_id": PATIENT_ID, "admission_date": _iso(discharge - timedelta(days=3)),
        "discharge_date": _iso(discharge), "reason": "Heart failure", "status": "active",
        "created_at": utcnow(),
    }, upsert=True)
    visit = today + timedelta(days=(7 - today.weekday()) % 7 or 7)  # next Monday
    _insert_obligation(
        db, patient_id=PATIENT_ID, episode_id=EPISODE_ID,
        description="Family physician visit", category="family_physician", owner="Dr. Shah, family physician",
        due_date=_iso(visit), status="scheduled", confirmed_date=_iso(visit),
        confirmed_source="Clinic booking", latest_evidence=f"Booked for {visit:%A %b %d} at 10:00 AM.",
        next_action="Confirm Grace can attend",
        quote="See family physician within 7 days of discharge.",
    )
    # Nobody has reached her: the 2-business-day contact window has closed, so
    # Continuum raises the same "post-discharge call" task the sweep would.
    state = tcm.clock(db, db.patients.find_one({"id": PATIENT_ID}, {"_id": 0}), today)
    db.patients.update_one({"id": PATIENT_ID}, {"$unset": {"sweep_marks": ""}})
    if state.get("eligible") and state.get("contact_status") == "missed":
        _create_task_approval(
            db, patient=db.patients.find_one({"id": PATIENT_ID}, {"_id": 0}),
            title="TCM contact window missed for Grace Thompson",
            rationale=(f"Discharged {state['discharge_date']}; interactive contact was due by {state['contact_due']} "
                       "and no attempts are documented. Call today and log the outcome."),
            changes={"item": "TCM contact", "contact_due": state["contact_due"]},
            obligation_id=f"tcm:{state['episode_id']}", evidence="0 documented attempt(s)", run_id=None,
        )
        db.patients.update_one({"id": PATIENT_ID}, {"$set": {"sweep_marks.tcm_missed": state["episode_id"]}})
    print(f"TCM contact: {state.get('contact_status')} (due {state.get('contact_due')})")
    digits = "".join(c for c in phone if c.isdigit())
    print(f"Grace Thompson ready: family physician visit {visit:%a %b %d} 10:00 AM; "
          f"date of birth May 14, 1958; phone ending {digits[-4:]}.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("+"):
        sys.exit("usage: stage_patient.py +<country><number>")
    main(sys.argv[1])
