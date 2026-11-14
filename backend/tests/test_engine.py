"""Product-rule regression tests for the Unclosed Loop Engine."""

from __future__ import annotations


def _obligation(client, description_fragment: str) -> dict:
    journey = client.get("/api/patients/pat_michael/journey").json()
    return next(
        o for o in journey["obligations"] if description_fragment.lower() in o["description"].lower()
    )


def test_day0_extracts_four_obligations(client):
    result = client.post("/api/demo/day/0", json={"day": 0}).json()
    assert result["status"] == "completed"
    obligations = client.get("/api/patients/pat_michael/journey").json()["obligations"]
    categories = {o["category"] for o in obligations}
    assert categories == {"family_physician", "bloodwork", "cardiology", "home_care"}
    assert all(o["due_date"] for o in obligations)
    created = [p for p in result["proposals"] if p["kind"] == "create_obligation"]
    assert len(created) == 4


def test_patient_report_is_not_verification(client):
    client.post("/api/demo/day/3", json={"day": 3})
    fp = _obligation(client, "Family physician")
    assert fp["status"] == "patient_reported"
    assert fp["status"] != "verified"
    pending = client.get("/api/agent/approvals", params={"patient_id": "pat_michael"}).json()["approvals"]
    assert any(a["kind"] == "verify_completion" for a in pending)

    evidence = client.get(f"/api/obligations/{fp['id']}/evidence").json()["evidence"]
    assert any(e["kind"] == "patient_report" for e in evidence)


def test_conflicting_date_is_preserved_and_resolved_by_approval(client):
    client.post("/api/demo/day/10", json={"day": 10})
    cardio = _obligation(client, "Cardiology")
    assert cardio["status"] == "conflict"
    assert len(cardio["conflicts"]) == 1
    conflict = cardio["conflicts"][0]
    assert conflict["recorded_value"] != conflict["reported_value"]

    approvals = client.get(
        "/api/agent/approvals", params={"patient_id": "pat_michael"}
    ).json()["approvals"]
    resolve = next(a for a in approvals if a["kind"] == "resolve_conflict")
    chosen = resolve["proposed_changes"]["confirmed_date"]

    response = client.post(
        f"/api/agent/approvals/{resolve['id']}/resolve",
        json={"approve": True, "actor": "Jordan", "note": "Clinic record authoritative"},
    ).json()
    assert response["status"] == "approved"

    cardio = _obligation(client, "Cardiology")
    assert cardio["status"] == "scheduled"
    assert cardio["confirmed_date"] == chosen
    assert cardio["conflicts"][0]["resolved"] is True
    assert cardio["conflicts"][0]["superseded"] == conflict["reported_value"]


def test_medication_question_escalates_without_clinical_advice(client, db):
    result = client.post("/api/demo/day/12", json={"day": 12}).json()
    assert result["status"] == "completed"
    reply = result["reply"].lower()
    assert "can't advise" in reply or "cannot advise" in reply
    assert "escalat" in reply
    # No dosage instruction may be produced.
    assert "double your" not in reply
    assert "increase your dose" not in reply

    # The escalation is created and routed to the CLINICIAN queue (not the
    # coordinator's operational queue).
    escalations = list(
        db.approvals.find(
            {"patient_id": "pat_michael", "kind": "escalate"}, {"_id": 0}
        )
    )
    assert escalations, "expected a clinical escalation to be created"
    assert all(a.get("audience") == "clinician" for a in escalations)

    # The coordinator must not see it in their (role-scoped) queue…
    coordinator_queue = client.get(
        "/api/agent/approvals", params={"patient_id": "pat_michael"}
    ).json()["approvals"]
    assert not any(a["kind"] == "escalate" for a in coordinator_queue)


def test_sensitive_chat_request_interrupts_and_resumes(client):
    response = client.post(
        "/api/agent/chat",
        json={
            "patient_id": "pat_michael",
            "message": "The lab confirmed the bloodwork is done. Please verify and close the bloodwork obligation.",
        },
    ).json()
    assert response["status"] == "awaiting_approval"
    assert response["interrupted"] is True

    thread = client.get("/api/agent/threads/pat_michael").json()
    assert thread["paused"] is True
    assert thread["next"] == ["await_approval"]

    interrupt_approval = next(
        a for a in thread["pending_approvals"] if a["mode"] == "interrupt"
    )
    resolved = client.post(
        f"/api/agent/approvals/{interrupt_approval['id']}/resolve",
        json={"approve": True, "actor": "Jordan", "note": "Lab fax reviewed"},
    ).json()
    assert resolved["status"] == "completed"

    bloodwork = _obligation(client, "Bloodwork")
    assert bloodwork["status"] == "verified"
    assert client.get("/api/agent/threads/pat_michael").json()["paused"] is False


def test_rejection_leaves_loop_open(client):
    response = client.post(
        "/api/agent/chat",
        json={
            "patient_id": "pat_michael",
            "message": "Please verify and close the bloodwork obligation.",
        },
    ).json()
    assert response["status"] == "awaiting_approval"
    approval = next(
        a
        for a in client.get("/api/agent/threads/pat_michael").json()["pending_approvals"]
        if a["mode"] == "interrupt"
    )
    client.post(
        f"/api/agent/approvals/{approval['id']}/resolve",
        json={"approve": False, "actor": "Jordan", "note": "No lab report on file"},
    )
    bloodwork = _obligation(client, "Bloodwork")
    assert bloodwork["status"] != "verified"


def test_status_endpoint_requires_evidence_for_closure(client):
    bloodwork = _obligation(client, "Bloodwork")
    response = client.post(
        f"/api/obligations/{bloodwork['id']}/status",
        json={"status": "verified", "actor": "Jordan"},
    )
    assert response.status_code == 422

    ok = client.post(
        f"/api/obligations/{bloodwork['id']}/status",
        json={
            "status": "verified",
            "actor": "Jordan",
            "evidence_summary": "Clinic called to confirm results reviewed on Nov 10.",
            "source_ref": "clinic-callback",
        },
    ).json()
    assert ok["obligation"]["status"] == "verified"


def test_voice_context_returns_open_obligations_and_disclosure(client):
    context = client.get("/api/voice/context/pat_michael").json()
    assert context["context"]
    assert "AI" in context["disclosure"]
    assert len(context["open_obligations"]) >= 3
    assert "clinical" in context["context"].lower()


def test_post_call_webhook_creates_event_and_updates(client):
    payload = {
        "type": "post_call_transcription",
        "data": {
            "conversation_id": "conv_test_1",
            "patient_id": "pat_michael",
            "transcript": [
                {"role": "agent", "message": "This is Continuum, an AI assistant."},
                {
                    "role": "user",
                    "message": "I already saw my family doctor last week.",
                },
            ],
        },
    }
    result = client.post("/api/voice/post-call", json=payload).json()
    assert result["ok"] is True
    calls = client.get("/api/voice/calls/pat_michael").json()["calls"]
    assert any(c["elevenlabs_conversation_id"] == "conv_test_1" for c in calls)
    events = client.get("/api/patients/pat_michael/journey").json()["events"]
    assert any(e["type"] == "voice_call" for e in events)
