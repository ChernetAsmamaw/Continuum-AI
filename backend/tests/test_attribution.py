"""Audit attribution: every write is recorded under the signed-in user.

The request body used to carry an ``actor`` string that defaulted to one
coordinator's name, so a clinician's decision was logged as the coordinator.
"""

from __future__ import annotations


def _sign_in(client, role: str) -> str:
    cred = client.get("/api/auth/demo").json()[role]
    token = client.post("/api/auth/login", json={"email": cred["email"], "password": cred["password"]}).json()["token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    client.headers["Authorization"] = f"Bearer {token}"
    return (me.get("user") or me)["name"]


def _latest_audit(client, patient_id: str) -> dict:
    return client.get(f"/api/agent/audit?patient_id={patient_id}&limit=1").json()["audit"][0]


def test_client_supplied_actor_is_ignored(client):
    name = _sign_in(client, "coordinator")
    obligation = client.get("/api/obligations?patient_id=pat_michael&open_only=true").json()["obligations"][0]
    response = client.post(
        f"/api/obligations/{obligation['id']}/evidence",
        json={"summary": "Clinic fax received", "actor": "Someone Else (care coordinator)"},
    )
    assert response.status_code == 200, response.text
    entry = _latest_audit(client, "pat_michael")
    assert entry["actor"] == f"{name} (care coordinator)"
    assert "Someone Else" not in entry["actor"]


def test_clinician_decision_is_logged_as_the_clinician(client):
    for day in (0, 3, 7, 10, 12):
        assert client.post(f"/api/demo/day/{day}").status_code == 200
    clinician = _sign_in(client, "clinician")
    pending = client.get("/api/agent/approvals?status=pending").json()["approvals"]
    assert pending, "day 12 should queue a clinician escalation"
    approval = pending[0]
    resolved = client.post(f"/api/agent/approvals/{approval['id']}/resolve", json={"approve": True, "note": "Reviewed"})
    assert resolved.status_code == 200, resolved.text
    # Demo days write future timestamps, so search the whole log, not the top.
    audit = client.get(f"/api/agent/audit?patient_id={approval['patient_id']}&limit=500").json()["audit"]
    decision = next(e for e in audit if e.get("action") == "Approval approved")
    assert decision["actor"] == f"{clinician} (clinician)"


def test_new_obligation_and_document_carry_the_user(client):
    name = _sign_in(client, "coordinator")
    created = client.post(
        "/api/obligations",
        json={"patient_id": "pat_sarah", "description": "Podiatry follow-up", "owner": "Podiatry clinic", "due_date": "2026-12-01"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["obligation"]["history"][0]["actor"] == f"{name} (care coordinator)"
