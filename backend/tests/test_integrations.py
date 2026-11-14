"""Clinic/lab FHIR intake closes loops on proof."""

from __future__ import annotations

MICHAEL = "pat_michael"
TOKEN = {"X-Continuum-Integration-Token": "test-integration-secret"}


def _obligation(client, category):
    obligations = client.get(f"/api/patients/{MICHAEL}/journey").json()["obligations"]
    return next(o for o in obligations if o["category"] == category)


def _post(client, resource, headers=TOKEN):
    return client.post("/api/integrations/fhir", json=resource, headers=headers)


def test_lab_result_attaches_proof_and_upgrades_the_pending_approval(client):
    client.post("/api/demo/day/0", json={"day": 0})
    report = {
        "resourceType": "DiagnosticReport",
        "id": "lab-1",
        "status": "final",
        "category": [{"coding": [{"display": "Laboratory"}]}],
        "code": {"text": "Creatinine and electrolytes panel"},
        "subject": {"reference": f"Patient/{MICHAEL}"},
        "effectiveDateTime": "2026-11-20T09:00:00Z",
    }
    body = _post(client, report).json()
    result = body["results"][0]
    assert result["outcome"] == "verification_ready"
    blood = _obligation(client, "bloodwork")
    assert result["obligation_id"] == blood["id"]
    approval = client.get(f"/api/agent/approvals/{result['approval_id']}").json()["approval"]
    assert approval["confidence"] == 0.95 and approval["clinic_evidence"]["source_ref"].startswith("DiagnosticReport/lab-1")

    # One click closes it, with the lab record as evidence.
    client.post(f"/api/agent/approvals/{approval['id']}/resolve", json={"approve": True, "actor": "Jordan"})
    assert _obligation(client, "bloodwork")["status"] == "verified"

    # Re-delivery of the same version is a no-op.
    assert _post(client, report).json()["results"][0]["outcome"] == "duplicate"


def test_patient_reported_visit_plus_encounter_reuses_one_card(client):
    client.post("/api/demo/day/3", json={"day": 3})
    encounter = {
        "resourceType": "Encounter",
        "id": "enc-7",
        "status": "finished",
        "type": [{"text": "Family physician follow-up visit"}],
        "subject": {"reference": "Patient/C-10482"},  # MRN works too
        "period": {"start": "2026-11-09T10:00:00Z"},
    }
    result = _post(client, encounter).json()["results"][0]
    assert result["outcome"] == "verification_ready"
    approvals = client.get("/api/agent/approvals", params={"patient_id": MICHAEL}).json()["approvals"]
    fp_cards = [a for a in approvals if a["kind"] == "verify_completion" and a["obligation_id"] == result["obligation_id"]]
    assert len(fp_cards) == 1 and fp_cards[0].get("clinic_evidence")


def test_booking_confirms_and_reschedule_supersedes(client):
    # Day-7 baseline: cardiology already confirmed by clinic record CS-8841.
    cardio = _obligation(client, "cardiology")
    appt = {
        "resourceType": "Appointment",
        "id": "appt-1",
        "status": "booked",
        "serviceType": [{"text": "Cardiology clinic follow-up"}],
        "participant": [{"actor": {"reference": f"Patient/{MICHAEL}"}}],
        "start": "2026-12-02T14:00:00Z",
    }
    result = _post(client, appt).json()["results"][0]
    assert result["obligation_id"] == cardio["id"]
    updated = _obligation(client, "cardiology")
    if cardio.get("confirmed_date") and cardio["confirmed_date"] != "2026-12-02":
        assert result["outcome"] == "rescheduled"
        assert updated["conflicts"][-1]["superseded"] == cardio["confirmed_date"]
    assert updated["confirmed_date"] == "2026-12-02"
    assert updated["status"] == "scheduled"


def test_noshow_and_cancel(client):
    client.post("/api/demo/day/0", json={"day": 0})
    base = {
        "resourceType": "Appointment",
        "serviceType": [{"text": "Home care assessment"}],
        "participant": [{"actor": {"reference": f"Patient/{MICHAEL}"}}],
        "start": "2026-11-18T14:00:00Z",
    }
    _post(client, {**base, "id": "hc-1", "status": "noshow"})
    assert _obligation(client, "home_care")["status"] in {"attempted", "overdue"}
    _post(client, {**base, "id": "hc-2", "status": "cancelled"})
    hc = _obligation(client, "home_care")
    assert "rebook" in (hc.get("next_action") or "").lower()


def test_unmatched_and_ignored_records(client):
    unmatched = _post(client, {
        "resourceType": "Encounter", "id": "e-x", "status": "finished",
        "type": [{"text": "Dermatology consult"}], "subject": {"reference": f"Patient/{MICHAEL}"},
    }).json()["results"][0]
    assert unmatched["outcome"] == "unmatched"
    ignored = _post(client, {"resourceType": "Encounter", "id": "e-y", "status": "in-progress"}).json()["results"][0]
    assert ignored["outcome"] == "ignored"
    unknown = _post(client, {
        "resourceType": "Encounter", "id": "e-z", "status": "finished", "subject": {"reference": "Patient/nobody"},
    }).json()["results"][0]
    assert unknown["outcome"] == "unmatched"


def test_auth(client):
    resource = {"resourceType": "Encounter", "id": "a", "status": "in-progress"}
    assert _post(client, resource, headers={"X-Continuum-Integration-Token": "wrong"}).status_code == 401
    assert _post(client, resource, headers={}).status_code == 200  # signed-in coordinator
    client.headers.pop("Authorization")
    assert _post(client, resource, headers={}).status_code == 401


def test_bundle(client):
    client.post("/api/demo/day/0", json={"day": 0})
    bundle = {
        "resourceType": "Bundle",
        "type": "transaction",
        "entry": [
            {"resource": {"resourceType": "Observation", "id": "o1", "status": "final",
                           "code": {"text": "Serum creatinine"}, "subject": {"reference": f"Patient/{MICHAEL}"},
                           "effectiveDateTime": "2026-11-15"}},
            {"resource": {"resourceType": "Patient", "id": "p"}},
        ],
    }
    body = _post(client, bundle).json()
    assert body["received"] == 2 and body["applied"] == 1
