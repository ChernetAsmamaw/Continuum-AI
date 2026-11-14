"""Escalation paging, acknowledgment, triage order, conflict date, bulk resolve."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import notify
from app.config import settings
from app.workflow import voice_escalate


def _sign_in(client, role: str) -> None:
    cred = client.get("/api/auth/demo").json()[role]
    token = client.post("/api/auth/login", json={"email": cred["email"], "password": cred["password"]}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"


@pytest.fixture()
def paging(monkeypatch):
    """Configured SMS + email with the network replaced by a recorder."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(settings, "twilio_auth_token", "token")
    monkeypatch.setattr(settings, "twilio_from_number", "+14165550100")
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_from", "continuum@test")
    monkeypatch.setattr(settings, "on_call_phone", "+14165550111")
    monkeypatch.setattr(settings, "on_call_email", "oncall@clinic.test")
    monkeypatch.setattr(notify, "send_sms", lambda to, body: sent.append(("sms", body)) or {"ok": True, "ref": "SM1", "detail": "SMS sent"})
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body: sent.append(("email", body)) or {"ok": True, "detail": "Email sent"})
    # Run the "background" page inline so the test can see it.
    monkeypatch.setattr(notify.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda self: target(*args)})())
    return sent


def _emergency(patient_id="pat_michael"):
    return voice_escalate(patient_id=patient_id, reason="Chest pain and short of breath since this morning", urgency="emergency")


def test_emergency_pages_on_call_by_sms_and_email(client, db, paging):
    result = _emergency()
    approval = db.approvals.find_one({"id": result["escalation_id"]})
    kinds = sorted(kind for kind, _ in paging)
    assert kinds == ["email", "sms"]
    assert "EMERGENCY" in paging[0][1]
    assert "Chest pain" not in paging[0][1]  # no clinical detail over SMS
    assert approval["pages"][0]["ok"] is True
    assert db.actions.find_one({"kind": "clinician_page", "patient_id": "pat_michael"})["status"] == "sent"


def test_routine_escalation_does_not_page(client, paging):
    voice_escalate(patient_id="pat_michael", reason="Question about a dose", urgency="routine")
    assert paging == []


def test_unconfigured_paging_is_recorded_as_failed(client, db):
    result = _emergency()
    approval = db.approvals.find_one({"id": result["escalation_id"]})
    assert approval["pages"][0]["ok"] is False
    assert db.actions.find_one({"kind": "clinician_page"})["status"] == "failed"


def test_repage_until_acknowledged(client, db, paging):
    approval_id = _emergency()["escalation_id"]
    later = datetime.now(timezone.utc) + timedelta(minutes=settings.page_ack_minutes_emergency + 1)
    assert [a["type"] for a in notify.repage_unacknowledged(db, later)] == ["clinician_repaged"]
    _sign_in(client, "clinician")
    assert client.post(f"/api/agent/approvals/{approval_id}/acknowledge").status_code == 200
    much_later = later + timedelta(hours=1)
    assert notify.repage_unacknowledged(db, much_later) == []
    assert db.actions.find_one({"kind": "clinician_page", "status": "confirmed"})


def test_pending_queue_is_triaged_by_urgency_then_age(client, paging):
    voice_escalate(patient_id="pat_sarah", reason="Dose question", urgency="routine")
    _emergency("pat_david")
    _sign_in(client, "clinician")
    approvals = client.get("/api/agent/approvals?status=pending").json()["approvals"]
    assert approvals[0]["priority"] == 0
    assert [a["priority"] for a in approvals] == sorted(a["priority"] for a in approvals)


def test_conflict_can_resolve_to_a_third_confirmed_date(client, db):
    for day in (0, 3, 7, 10):
        assert client.post(f"/api/demo/day/{day}").status_code == 200
    conflict = next(a for a in client.get("/api/agent/approvals?status=pending").json()["approvals"] if a["kind"] == "resolve_conflict")
    response = client.post(f"/api/agent/approvals/{conflict['id']}/resolve", json={"approve": True, "chosen_date": "2026-12-02"})
    assert response.status_code == 200, response.text
    obligation = db.obligations.find_one({"id": conflict["obligation_id"]})
    assert obligation["confirmed_date"] == "2026-12-02"
    assert obligation["confirmed_source"] == "coordinator-confirmed date"
    assert all(c.get("resolved") for c in obligation.get("conflicts") or [])


def test_chosen_date_is_refused_outside_conflicts(client):
    approval_id = _emergency()["escalation_id"]
    _sign_in(client, "clinician")
    response = client.post(f"/api/agent/approvals/{approval_id}/resolve", json={"approve": True, "chosen_date": "2026-12-02"})
    assert response.status_code == 422


def test_bulk_resolve_respects_queues(client):
    a = voice_escalate(patient_id="pat_sarah", reason="Dose question", urgency="routine")["escalation_id"]
    b = voice_escalate(patient_id="pat_amina", reason="Inhaler question", urgency="routine")["escalation_id"]
    # Coordinator can't resolve clinician escalations, even in bulk.
    refused = client.post("/api/agent/approvals/bulk-resolve", json={"ids": [a, b], "approve": True}).json()
    assert refused["resolved"] == 0
    _sign_in(client, "clinician")
    done = client.post("/api/agent/approvals/bulk-resolve", json={"ids": [a, b], "approve": True, "note": "Called both"}).json()
    assert done["resolved"] == 2


def test_failed_page_is_not_reported_as_paged(client, db):
    result = _emergency()  # no ON_CALL_PHONE / ON_CALL_EMAIL in tests
    assert result["ok"] is True and result["paged"] is False and result["page_status"] == "failed"
    assert "911" in result["spoken_response"]
    assert "clinician" not in result["spoken_response"].lower()  # true whether or not a page went out
    assert "NOT be paged" in result["agent_instructions"]
    approval = db.approvals.find_one({"id": result["escalation_id"]})
    assert approval["page_failed"] is True and "Paging failed" in approval["page_note"]
    # Coordinators get a task to reach the on-call clinician, once per escalation.
    tasks = list(db.approvals.find({"obligation_id": f"page_failed:{approval['id']}", "status": "pending"}))
    assert len(tasks) == 1 and tasks[0]["audience"] == "coordinator" and tasks[0]["source"] == "paging"


def test_successful_page_reports_paged(client, db, paging):
    result = _emergency()
    assert result["paged"] is True and result["page_status"] == "sent"
    assert db.approvals.find_one({"id": result["escalation_id"]})["page_failed"] is False


def test_routine_escalation_is_not_paged_and_says_so(client, paging):
    result = voice_escalate(patient_id="pat_michael", reason="Question about a dose", urgency="routine")
    assert result["paged"] is False and result["page_status"] == "not_required"
    assert result["spoken_response"] == "I've flagged this for your care team to review and follow up with you."


def test_second_escalation_keeps_its_reason_and_the_highest_urgency(client, db, paging):
    first = _emergency()
    second = voice_escalate(patient_id="pat_michael", reason="Also asks about refilling metoprolol", urgency="routine")
    assert second["escalation_id"] == first["escalation_id"]  # one card per patient
    approval = db.approvals.find_one({"id": first["escalation_id"]})
    assert "Chest pain" in approval["rationale"] and "metoprolol" in approval["rationale"]
    assert approval["proposed_changes"]["urgency"] == "emergency" and second["urgency"] == "emergency"
    assert [r["urgency"] for r in approval["reasons"]] == ["emergency", "routine"]
    assert len(approval["pages"]) == 1  # a routine addition does not re-page
    # A retried identical call adds nothing.
    voice_escalate(patient_id="pat_michael", reason="Also asks about refilling metoprolol", urgency="routine")
    assert len(db.approvals.find_one({"id": first["escalation_id"]})["reasons"]) == 2


def test_routine_escalation_raised_to_urgent_pages(client, db, paging):
    first = voice_escalate(patient_id="pat_michael", reason="Question about a dose", urgency="routine")
    assert paging == []
    second = voice_escalate(patient_id="pat_michael", reason="Now short of breath", urgency="urgent")
    approval = db.approvals.find_one({"id": first["escalation_id"]})
    assert second["escalation_id"] == first["escalation_id"]
    assert approval["proposed_changes"]["urgency"] == "urgent" and approval["title"] == "Clinical escalation (urgent)"
    assert approval["pages"][0]["ok"] is True and second["paged"] is True
