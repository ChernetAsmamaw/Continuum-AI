"""LangGraph flow (interrupts, resume, guardrails) and the voice tool endpoints."""

from __future__ import annotations

from app.config import settings

MICHAEL = "pat_michael"


def _approvals(client, **params):
    return client.get("/api/agent/approvals", params={"patient_id": MICHAEL, **params}).json()["approvals"]


def _login(client, role: str) -> None:
    creds = client.get("/api/auth/demo").json()[role]
    token = client.post("/api/auth/login", json={"email": creds["email"], "password": creds["password"]}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"


# ── Agent graph ──────────────────────────────────────────────────────────


def test_explicit_close_request_pauses_the_thread_until_approved(client):
    client.post("/api/demo/day/3", json={"day": 3})
    result = client.post(
        "/api/agent/chat",
        json={"patient_id": MICHAEL, "message": "Please verify and close the family physician follow-up."},
    ).json()
    assert result["status"] == "awaiting_approval", result
    thread = client.get(f"/api/agent/threads/{MICHAEL}").json()
    assert thread["paused"] is True

    # While paused, new chat is refused rather than silently queued.
    blocked = client.post("/api/agent/chat", json={"patient_id": MICHAEL, "message": "Anything else?"}).json()
    assert blocked["status"] == "awaiting_approval"

    interrupt = next(a for a in _approvals(client) if a["mode"] == "interrupt")
    resumed = client.post(
        f"/api/agent/approvals/{interrupt['id']}/resolve",
        json={"approve": True, "actor": "Jordan", "note": "Clinic fax received"},
    ).json()
    assert resumed["status"] == "completed", resumed
    assert client.get(f"/api/agent/threads/{MICHAEL}").json()["paused"] is False
    fp = next(
        o for o in client.get(f"/api/patients/{MICHAEL}/journey").json()["obligations"]
        if o["category"] == "family_physician"
    )
    assert fp["status"] == "verified"


def test_rejecting_an_interrupt_keeps_the_loop_open(client):
    client.post("/api/demo/day/3", json={"day": 3})
    client.post("/api/agent/chat", json={"patient_id": MICHAEL, "message": "Verify the family physician visit."})
    interrupt = next(a for a in _approvals(client) if a["mode"] == "interrupt")
    client.post(f"/api/agent/approvals/{interrupt['id']}/resolve", json={"approve": False, "actor": "Jordan"})
    fp = next(
        o for o in client.get(f"/api/patients/{MICHAEL}/journey").json()["obligations"]
        if o["category"] == "family_physician"
    )
    assert fp["status"] != "verified"


def test_medication_question_routes_to_the_clinician_queue_only(client):
    result = client.post("/api/demo/day/12", json={"day": 12}).json()
    assert "dose" not in (result.get("reply") or "").lower() or "can't" in result["reply"].lower()
    coordinator_view = _approvals(client)
    assert not any(a["kind"] == "escalate" for a in coordinator_view)

    _login(client, "clinician")
    escalations = [a for a in _approvals(client) if a["kind"] == "escalate"]
    assert escalations
    assert client.post(
        f"/api/agent/approvals/{escalations[0]['id']}/resolve", json={"approve": True, "actor": "Dr. Osei"}
    ).status_code == 200


def test_coordinator_cannot_resolve_clinician_items(client):
    client.post("/api/demo/day/12", json={"day": 12})
    _login(client, "clinician")
    escalation = next(a for a in _approvals(client) if a["kind"] == "escalate")
    _login(client, "coordinator")
    response = client.post(f"/api/agent/approvals/{escalation['id']}/resolve", json={"approve": True})
    assert response.status_code == 403


def test_every_run_writes_audit_and_checkpoint(client):
    client.post("/api/demo/day/0", json={"day": 0})
    audit = client.get("/api/agent/audit", params={"patient_id": MICHAEL}).json()
    entries = audit.get("audit") or audit.get("entries") or []
    assert any(e["action"] == "Updated obligation" or "obligation" in e["action"].lower() for e in entries)
    snapshot = client.get(f"/api/agent/threads/{MICHAEL}").json()
    assert snapshot.get("values") or snapshot.get("state") or snapshot.get("checkpoint") or snapshot


# ── Voice tools ──────────────────────────────────────────────────────────


def test_identify_returns_no_clinical_details(client):
    body = client.post("/api/voice/identify", json={"given_name": "Michael", "family_name": "Chen"}).json()
    candidate = next(c for c in body["matches"] if c["patient_id"] == MICHAEL)
    assert set(candidate) >= {"name", "phone_last4", "consent_to_recording"}
    assert "mrn" not in candidate and "condition" not in candidate


def test_voice_report_is_evidence_not_verification(client):
    client.post("/api/demo/day/0", json={"day": 0})
    client.post(
        "/api/voice/report",
        json={"patient_id": MICHAEL, "report": "user: I went to the cardiology clinic on 2026-11-20."},
    )
    cardio = next(
        o for o in client.get(f"/api/patients/{MICHAEL}/journey").json()["obligations"] if o["category"] == "cardiology"
    )
    assert cardio["status"] in {"patient_reported", "conflict"}


def test_urgent_escalation_goes_to_clinicians(client):
    body = client.post(
        "/api/voice/escalate",
        json={"patient_id": MICHAEL, "reason": "Short of breath since this morning", "urgency": "urgent"},
    ).json()
    assert body.get("ok", True)
    _login(client, "clinician")
    assert any(a["kind"] == "escalate" for a in _approvals(client))


def test_booking_request_needs_coordinator_confirmation(client):
    body = client.post(
        "/api/voice/appointment",
        json={"patient_id": MICHAEL, "requested_time": "next Tuesday morning", "notes": "home care"},
    ).json()
    assert body["ok"] is True
    assert "booked" not in body.get("spoken_response", "").lower() or "not" in body["spoken_response"].lower()
    assert any(a["kind"] == "schedule" for a in _approvals(client))


def test_callback_request_schedules_a_checkin_the_sweep_will_place(client, db):
    from datetime import date, datetime, timezone

    from app.scheduler import run_sweep

    body = client.post(
        "/api/voice/checkin", json={"patient_id": MICHAEL, "when": "today", "reason": "Call me back"}
    ).json()
    assert body["ok"] is True
    checkin = db.checkins.find_one({"patient_id": MICHAEL, "reason": "Call me back"})
    assert checkin["scheduled_for"] == date.today().isoformat()  # loose time resolved at request time
    run_sweep(today=date.today(), now=datetime.now(timezone.utc))
    assert db.checkins.find_one({"id": checkin["id"]})["status"] == "awaiting_manual_call"


def test_post_call_webhook_runs_the_graph_and_closes_the_checkin(client, db):
    from app import repository as repo

    checkin = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": "2026-11-13", "reason": "x"})
    db.checkins.update_one({"id": checkin["id"]}, {"$set": {"status": "dispatched"}})
    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_abc",
            "conversation_initiation_client_data": {
                "dynamic_variables": {"patient_id": MICHAEL, "checkin_id": checkin["id"]}
            },
            "transcript": [
                {"role": "agent", "message": "Did you get your bloodwork done?"},
                {"role": "user", "message": "Not yet, I'm going next week."},
            ],
        },
    }
    body = client.post("/api/voice/post-call", json=payload).json()
    assert body["ok"] is True, body
    assert db.checkins.find_one({"id": checkin["id"]})["status"] == "completed"


def test_voice_token_is_enforced_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "continuum_voice_secret", "s3cret")
    denied = client.post("/api/voice/escalate", json={"patient_id": MICHAEL, "reason": "x"})
    assert denied.status_code == 401
    allowed = client.post(
        "/api/voice/escalate", json={"patient_id": MICHAEL, "reason": "x"}, headers={"x-continuum-token": "s3cret"}
    )
    assert allowed.status_code == 200


def test_signed_post_call_needs_no_token_and_finds_patient_from_tool_calls(client, db, monkeypatch):
    import hashlib
    import hmac
    import json
    import time

    monkeypatch.setattr(settings, "continuum_voice_secret", "tok")
    monkeypatch.setattr(settings, "elevenlabs_webhook_secret", "wsec_test")
    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_widget_1",
            # Browser/widget calls send nulls here; they must not crash the webhook.
            "metadata": {"phone_call": None, "call_duration_secs": 42},
            "conversation_initiation_client_data": {"dynamic_variables": None},
            "transcript": [
                {"role": "agent", "message": "Hi, who am I speaking with?"},
                {"role": "user", "message": "Michael Chen."},
                {"role": "agent", "message": None,
                 "tool_calls": [{"tool_name": "get_patient_context", "params_as_json": "{\"patient_id\": \"pat_michael\"}"}]},
                {"role": "agent", "message": "Have you had your bloodwork?"},
                {"role": "user", "message": "Not yet. Bye!"},
            ],
        },
    }
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(b"wsec_test", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()

    bad = client.post("/api/voice/post-call", content=body,
                      headers={"content-type": "application/json", "ElevenLabs-Signature": f"t={ts},v0=deadbeef"})
    assert bad.status_code == 401

    ok = client.post("/api/voice/post-call", content=body,
                     headers={"content-type": "application/json", "ElevenLabs-Signature": f"t={ts},v0={sig}"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["patient_id"] == MICHAEL
    calls = client.get(f"/api/voice/calls/{MICHAEL}", headers={"x-continuum-token": "tok"}).json()
    listed = calls.get("calls") or calls.get("voice_calls") or calls
    assert any(c.get("elevenlabs_conversation_id") == "conv_widget_1" for c in listed)


def test_calls_are_processed_while_a_chat_is_paused(client, db):
    client.post("/api/demo/day/3", json={"day": 3})
    paused = client.post(
        "/api/agent/chat", json={"patient_id": MICHAEL, "message": "Verify and close the family physician follow-up."}
    ).json()
    assert paused["status"] == "awaiting_approval"
    body = client.post(
        "/api/voice/post-call",
        json={"data": {"conversation_id": "conv_while_paused", "dynamic_variables": {"patient_id": MICHAEL},
                       "transcript": [{"role": "agent", "message": "Did you get your bloodwork done?"},
                                      {"role": "user", "message": "Yes, I did my bloodwork on 2026-11-12."}]}},
    ).json()
    assert body["status"] == "completed", body
    blood = next(o for o in client.get(f"/api/patients/{MICHAEL}/journey").json()["obligations"] if o["category"] == "bloodwork")
    assert blood["status"] == "patient_reported"
    assert client.get(f"/api/agent/threads/{MICHAEL}").json()["paused"] is True  # chat still waits for its approval


_AGENT_DIRECTIVES = ("do not", "don't tell", "tell the patient", "the patient")


def _patient_safe(text: str) -> bool:
    return not any(d in text.lower() for d in _AGENT_DIRECTIVES)


def test_spoken_responses_hold_no_agent_instructions(client, db):
    booking = client.post("/api/voice/appointment", json={"patient_id": MICHAEL, "requested_time": "Friday morning"}).json()
    follow = client.post("/api/voice/follow-up", json={"patient_id": MICHAEL, "item": "a copy of my discharge summary"}).json()
    callback = client.post("/api/voice/checkin", json={"patient_id": MICHAEL, "when": "Friday afternoon",
                                                       "reason": "Patient requested a callback on Friday afternoon"}).json()
    for body in (booking, follow, callback):
        assert _patient_safe(body["spoken_response"]), body["spoken_response"]
        assert body["agent_instructions"]
    assert "NOT booked" in booking["agent_instructions"]
    assert "requested" not in callback["spoken_response"].lower()  # the reason is not echoed back
    assert ".." not in callback["spoken_response"]


def test_report_acknowledgement_is_a_fixed_template(client):
    body = client.post("/api/voice/report", json={"patient_id": MICHAEL, "report": "I missed my cardiology visit."}).json()
    assert body["spoken_response"] == "Thanks, I've noted that for your care team; the clinic will verify it."
    assert body["reply"] == body["spoken_response"]


def test_callback_time_is_resolved_and_spoken_back(client, db):
    from app.db import demo_today

    body = client.post("/api/voice/checkin", json={"patient_id": MICHAEL, "when": "Friday afternoon"}).json()
    assert body["resolved"] is True and body["status"] == "scheduled"
    assert "T14:00:00" in body["scheduled_for"]
    assert body["spoken_response"].startswith("I've set up a call for Friday, ") and "at 2 PM" in body["spoken_response"]
    stored = db.checkins.find_one({"id": body["checkin_id"]})
    assert stored["scheduled_for_raw"] == "Friday afternoon"
    assert stored["scheduled_for"][:10] > demo_today().isoformat()


def test_unresolved_callback_time_is_not_claimed_as_set(client, db):
    body = client.post("/api/voice/checkin", json={"patient_id": MICHAEL, "when": "whenever suits the nurse"}).json()
    assert body["ok"] is True and body["resolved"] is False and body["scheduled_for"] is None
    assert "confirm the time" in body["spoken_response"]
    assert "set up a call for" not in body["spoken_response"]


def test_date_alone_does_not_pick_the_wrong_follow_up():
    from app.engine import rank_obligations

    obligations = [
        {"id": "o_home", "description": "Home-care assessment", "category": "home_care", "status": "pending",
         "due_date": "2026-11-12", "keywords": ["home care", "nurse visit"], "owner": "Home care agency"},
        {"id": "o_blood", "description": "Bloodwork (creatinine and electrolytes)", "category": "bloodwork",
         "status": "verified", "due_date": "2026-11-18", "keywords": ["bloodwork", "blood work"], "owner": "Laboratory"},
    ]
    ranked = rank_obligations(obligations, "Patient said he did his blood work yesterday.", "bloodwork",
                              reported_date="2026-11-13")
    assert all(o["id"] != "o_home" for o, _, _ in ranked)


def test_repeated_identical_voice_tool_call_is_not_recorded_twice(client):
    from app.db import get_db

    body = {"patient_id": MICHAEL, "item": "Medication list", "conversation_id": "conv_repeat_test"}
    first = client.post("/api/voice/follow-up", json=body).json()
    before = get_db().approvals.count_documents({"patient_id": MICHAEL})
    second = client.post("/api/voice/follow-up", json=body).json()
    assert second.get("repeat") is True
    assert get_db().approvals.count_documents({"patient_id": MICHAEL}) == before
    assert first.get("spoken_response") == second.get("spoken_response")


def test_voice_tags_are_stripped_from_agent_lines():
    from app.util import strip_voice_tags

    assert strip_voice_tags("[happy] Thanks, Michael. [slow] Great.") == "Thanks, Michael. Great."
