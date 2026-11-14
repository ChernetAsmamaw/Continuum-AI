"""Text / email / portal check-ins, the DOB-gated check-in page, inbound SMS."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app import messaging, notify
from app.config import settings
from app.db import demo_today
from app.scheduler import dispatch_due_checkins

ROSA = {
    "name": "Rosa Delgado", "birth_date": "1961-04-12", "phone": "+1 416 555 0144", "email": "rosa@example.com",
    "discharge_date": demo_today().isoformat(), "consent_to_automated_calls": True,
    "obligations": [{"description": "Cardiology follow-up", "owner": "Cardiology clinic"}],
}


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(settings, "twilio_account_sid", "AC_test")
    monkeypatch.setattr(settings, "twilio_auth_token", "token")
    monkeypatch.setattr(settings, "twilio_from_number", "+14165550100")
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_from", "care@clinic.test")
    monkeypatch.setattr(notify, "send_sms", lambda to, body: sent.append(("sms", to, body)) or {"ok": True, "detail": "SMS sent"})
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body: sent.append(("email", to, body)) or {"ok": True, "detail": "Email sent"})
    return sent


def _patient(client, channel):
    data = client.post("/api/patients", json={**ROSA, "channel": channel}).json()
    return data["patient"]["id"]


def _dispatch(db):
    noon = datetime.combine(demo_today(), datetime.min.time()).replace(hour=16, tzinfo=timezone.utc)
    return dispatch_due_checkins(db, demo_today(), noon)


@pytest.mark.parametrize("channel,via", [("Text", "sms"), ("Email", "email"), ("Portal", "email")])
def test_due_check_in_goes_out_on_the_chosen_channel(client, db, outbox, channel, via):
    pid = _patient(client, channel)
    db.checkins.update_many({"patient_id": pid}, {"$set": {"scheduled_for": demo_today().isoformat()}})
    _dispatch(db)
    assert [kind for kind, _, _ in outbox] == [via]
    body = outbox[0][2]
    assert "/checkin/" in body and "Cardiology" not in body  # no clinical detail in the message
    assert db.contact_attempts.find_one({"patient_id": pid, "outcome": "message_sent"})


def test_without_consent_nothing_is_sent(client, db, outbox):
    data = client.post("/api/patients", json={**ROSA, "channel": "Text", "consent_to_automated_calls": False}).json()
    db.checkins.update_many({"patient_id": data["patient"]["id"]}, {"$set": {"scheduled_for": demo_today().isoformat()}})
    _dispatch(db)
    assert outbox == []


def test_check_in_page_needs_dob_then_records_patient_reports(client, db):
    pid = _patient(client, "Portal")
    patient = db.patients.find_one({"id": pid}, {"_id": 0})
    token = messaging.checkin_link(patient).rsplit("/", 1)[1]

    assert client.get(f"/api/portal/checkin/{token}").json()["verify_with"] == "birth_date"
    assert client.post("/api/portal/checkin/verify", json={"token": token, "answer": "1961-04-13"}).status_code == 403
    verified = client.post("/api/portal/checkin/verify", json={"token": token, "answer": "1961-04-12"}).json()
    item = next(f for f in verified["follow_ups"] if f["description"] == "Cardiology follow-up")
    assert all(f["description"] != "Medication reconciliation" for f in verified["follow_ups"])

    done = client.post(
        "/api/portal/checkin/submit",
        json={"session": verified["session"], "updates": [{"obligation_id": item["id"], "status": "booked", "date": "2026-12-03"}], "callback": True},
    )
    assert done.status_code == 200, done.text
    ob = db.obligations.find_one({"id": item["id"]})
    assert ob["status"] != "verified"  # a patient's word is never proof
    assert db.contact_attempts.find_one({"patient_id": pid, "outcome": "reached", "channel": "Portal"})
    assert db.checkins.find_one({"patient_id": pid, "reason": {"$regex": "call back"}})


def test_check_in_tokens_cannot_open_the_console_api(client):
    patient = client.post("/api/patients", json=ROSA).json()["patient"]
    token = messaging.checkin_link(patient).rsplit("/", 1)[1]
    response = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_dob_attempts_lock_after_five(client, db):
    patient = client.post("/api/patients", json=ROSA).json()["patient"]
    token = messaging.checkin_link(patient).rsplit("/", 1)[1]
    for _ in range(5):
        client.post("/api/portal/checkin/verify", json={"token": token, "answer": "1900-01-01"})
    locked = client.post("/api/portal/checkin/verify", json={"token": token, "answer": "1961-04-12"})
    assert locked.status_code == 423


def test_inbound_text_stop_opts_out_and_replies_are_reports(client, db, monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", None)  # development: unsigned allowed
    pid = _patient(client, "Text")
    reply = client.post("/api/messages/sms/inbound", data={"From": "+14165550144", "Body": "I saw cardiology yesterday", "MessageSid": "SM1"})
    assert reply.status_code == 200 and "<Message>" in reply.text
    assert db.contact_attempts.find_one({"patient_id": pid, "channel": "Text", "outcome": "reached"})
    stop = client.post("/api/messages/sms/inbound", data={"From": "+14165550144", "Body": "STOP", "MessageSid": "SM2"})
    assert "unsubscribed" in stop.text
    assert db.patients.find_one({"id": pid})["preferences"]["consent_to_automated_calls"] is False


def test_twilio_signature_is_checked(monkeypatch):
    monkeypatch.setattr(settings, "twilio_auth_token", "secret")
    import base64
    import hashlib
    import hmac

    url, params = "https://x.test/api/messages/sms/inbound", {"Body": "hi", "From": "+1"}
    good = base64.b64encode(hmac.new(b"secret", (url + "BodyhiFrom+1").encode(), hashlib.sha1).digest()).decode()
    assert messaging.twilio_signature_ok(url, params, good)
    assert not messaging.twilio_signature_ok(url, params, "bad")
