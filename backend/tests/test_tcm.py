"""TCM clock: 2-business-day contact, 7/14-day visit, documented attempts."""

from __future__ import annotations

from datetime import date

from app import tcm
from app.db import demo_today
from app.scheduler import retry_tcm_contacts


def test_business_days_skip_the_weekend():
    friday = date(2026, 11, 13)
    assert tcm.add_business_days(friday, 2) == date(2026, 11, 17)  # Tuesday
    assert tcm.add_business_days(date(2026, 11, 10), 2) == date(2026, 11, 12)


def test_visit_window_follows_risk_unless_overridden():
    assert tcm.visit_window_days({"risk": "High"}) == 7
    assert tcm.visit_window_days({"risk": "Medium"}) == 14
    assert tcm.visit_window_days({"risk": "Low", "tcm": {"visit_window_days": 7}}) == 7


def test_facility_discharge_has_no_clock():
    assert tcm.is_eligible({"discharge_date": "2026-11-10", "disposition": "Skilled nursing facility"})[0] is False
    assert tcm.is_eligible({"discharge_date": "2026-11-10", "disposition": "Home"})[0] is True


def _intake(client, **extra):
    body = {"name": "Rosa Delgado", "risk": "High", "discharge_date": demo_today().isoformat(), **extra}
    response = client.post("/api/patients", json=body)
    assert response.status_code == 200, response.text
    return response.json()["patient"]["id"]


def test_intake_books_the_first_contact_and_shows_the_clock(client, db):
    pid = _intake(client)
    checkins = list(db.checkins.find({"patient_id": pid, "kind": "tcm_contact"}))
    assert len(checkins) == 1
    state = client.get(f"/api/patients/{pid}/journey").json()["tcm"]
    assert state["eligible"] is True
    assert state["visit_window_days"] == 7 and state["code"] == "99496"
    assert state["contact_status"] in ("open", "due_today")
    assert state["contact_due"] == tcm.add_business_days(demo_today(), 2).isoformat()


def test_two_failed_attempts_satisfy_the_contact_requirement(client):
    pid = _intake(client)
    client.post(f"/api/patients/{pid}/contacts", json={"outcome": "no_answer"})
    state = client.post(f"/api/patients/{pid}/contacts", json={"outcome": "voicemail", "note": "Left callback number"}).json()["tcm"]
    assert state["contact_attempts"] == 2
    assert state["contact_status"] == "attempts_documented"


def test_reaching_the_patient_meets_the_requirement(client):
    pid = _intake(client)
    state = client.post(f"/api/patients/{pid}/contacts", json={"outcome": "reached"}).json()["tcm"]
    assert state["contact_status"] == "met"


def test_only_a_clinician_can_change_the_visit_window(client):
    pid = _intake(client, risk="Low")
    assert client.put(f"/api/patients/{pid}/tcm/visit-window", json={"days": 7}).status_code == 403
    cred = client.get("/api/auth/demo").json()["clinician"]
    token = client.post("/api/auth/login", json={"email": cred["email"], "password": cred["password"]}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    state = client.put(f"/api/patients/{pid}/tcm/visit-window", json={"days": 7}).json()["tcm"]
    assert state["visit_window_days"] == 7 and state["visit_window_source"] == "clinician"


def test_sweep_retries_after_a_failed_attempt_then_flags_a_missed_window(client, db):
    pid = _intake(client)
    db.checkins.update_many({"patient_id": pid, "kind": "tcm_contact"}, {"$set": {"status": "completed"}})
    client.post(f"/api/patients/{pid}/contacts", json={"outcome": "no_answer"})
    actions = retry_tcm_contacts(db, demo_today())
    assert any(a["type"] == "tcm_contact_retry" and a["patient_id"] == pid for a in actions)

    later = tcm.add_business_days(demo_today(), 3)
    actions = retry_tcm_contacts(db, later)
    assert any(a["type"] == "tcm_contact_missed" and a["patient_id"] == pid for a in actions)
    assert not any(a["type"] == "tcm_contact_missed" and a["patient_id"] == pid for a in retry_tcm_contacts(db, later))


def test_post_call_with_patient_speech_counts_as_contact(client, db):
    from app.workflow import handle_post_call

    pid = _intake(client)
    handle_post_call(
        {
            "data": {
                "conversation_id": "conv_tcm_1",
                "transcript": [
                    {"role": "agent", "message": "Hi, this is Continuum."},
                    {"role": "user", "message": "Hi, I'm doing okay since I got home."},
                ],
            }
        },
        patient_id=pid,
    )
    contacts = list(db.contact_attempts.find({"patient_id": pid}))
    assert len(contacts) == 1 and contacts[0]["outcome"] == "reached"


def test_worklist_lists_patients_needing_contact(client):
    pid = _intake(client)
    lanes = client.get("/api/tcm/worklist").json()["lanes"]
    assert any(row["patient_id"] == pid for row in lanes["contact_due"])
