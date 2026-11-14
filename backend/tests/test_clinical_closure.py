"""Medication reconciliation (clinician attestation) and closed-loop referrals."""

from __future__ import annotations

from app.db import demo_today
from app.tcm import visit_window_days


def _sign_in(client, role: str) -> None:
    cred = client.get("/api/auth/demo").json()[role]
    token = client.post("/api/auth/login", json={"email": cred["email"], "password": cred["password"]}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"


def _intake(client, **extra):
    body = {"name": "Rosa Delgado", "risk": "Medium", "discharge_date": demo_today().isoformat(), **extra}
    data = client.post("/api/patients", json=body).json()
    return data["patient"]["id"], data["obligations"]


def test_every_tcm_episode_gets_a_clinician_med_rec_due_by_the_visit(client):
    _, obligations = _intake(client)
    med_rec = [o for o in obligations if o["category"] == "medication_reconciliation"]
    assert len(med_rec) == 1
    assert med_rec[0]["owner"] == "Clinician"
    from datetime import timedelta

    assert med_rec[0]["due_date"] == (demo_today() + timedelta(days=visit_window_days({"risk": "Medium"}))).isoformat()


def test_only_a_clinician_can_close_med_rec_and_must_attest(client):
    _, obligations = _intake(client)
    med_rec = next(o for o in obligations if o["category"] == "medication_reconciliation")
    url = f"/api/obligations/{med_rec['id']}/status"
    coordinator = client.post(url, json={"status": "verified", "override_reason": "Patient says it is fine"})
    assert coordinator.status_code == 403
    _sign_in(client, "clinician")
    assert client.post(url, json={"status": "verified"}).status_code == 422
    attested = client.post(url, json={"status": "verified", "evidence_summary": "Compared discharge and home lists; stopped duplicate furosemide."})
    assert attested.status_code == 200, attested.text


def test_med_rec_approvals_go_to_the_clinician_queue(client, db):
    from app import repository as repo

    _, obligations = _intake(client)
    med_rec = next(o for o in obligations if o["category"] == "medication_reconciliation")
    approval = repo.create_approval(db, {"patient_id": med_rec["patient_id"], "kind": "verify_completion", "obligation_id": med_rec["id"], "title": "Close med rec"})
    assert approval["audience"] == "clinician"


def test_referral_needs_the_consult_note_to_close(client, db):
    pid, _ = _intake(client)
    ob = client.post("/api/obligations", json={"patient_id": pid, "description": "Cardiology referral", "due_date": "2026-12-01"}).json()["obligation"]
    db.obligations.update_one({"id": ob["id"]}, {"$set": {"category": "cardiology"}})
    url = f"/api/obligations/{ob['id']}/status"
    assert client.post(url, json={"status": "verified", "evidence_summary": "Seen 20 Nov"}).status_code == 422
    closed = client.post(url, json={"status": "verified", "evidence_summary": "Seen 20 Nov", "consult_note_ref": "FAX-2231"})
    assert closed.status_code == 200, closed.text


def test_finished_visit_waits_for_the_consult_note(client, db):
    pid, _ = _intake(client)
    ob = client.post("/api/obligations", json={"patient_id": pid, "description": "Cardiology referral", "due_date": "2026-12-01"}).json()["obligation"]
    db.obligations.update_one({"id": ob["id"]}, {"$set": {"category": "cardiology", "fhir_refs": ["ServiceRequest/ref-1"]}})
    headers = {"X-Continuum-Integration-Token": "test-integration-secret"}
    encounter = {
        "resourceType": "Encounter", "id": "enc-1", "status": "finished",
        "subject": {"reference": f"Patient/{pid}"}, "basedOn": [{"reference": "ServiceRequest/ref-1"}],
        "period": {"start": "2026-11-20"}, "type": [{"text": "Cardiology consult"}],
    }
    first = client.post("/api/integrations/fhir", json=encounter, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["results"][0]["outcome"] == "awaiting_consult_note"
    note = {
        "resourceType": "DocumentReference", "id": "doc-1", "status": "current",
        "subject": {"reference": f"Patient/{pid}"}, "type": {"text": "Cardiology consult note"},
        "context": {"related": [{"reference": "ServiceRequest/ref-1"}]}, "date": "2026-11-21",
    }
    second = client.post("/api/integrations/fhir", json=note, headers=headers).json()["results"][0]
    assert second["outcome"] == "verification_ready"
    assert db.obligations.find_one({"id": ob["id"]})["consult_note_ref"].startswith("DocumentReference/doc-1")
