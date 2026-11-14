"""Assignment to real people and the My work list."""

from __future__ import annotations

from app.db import demo_today


def _users(client):
    return {u["role"]: u for u in client.get("/api/users").json()["users"]}


def test_assign_follow_up_and_see_it_in_my_work(client):
    me = client.get("/api/auth/me").json()
    me = me.get("user") or me
    ob = client.get("/api/obligations?patient_id=pat_michael&open_only=true").json()["obligations"][0]
    response = client.post(f"/api/obligations/{ob['id']}/assign", json={"user_id": me["id"]})
    assert response.status_code == 200, response.text
    assert response.json()["obligation"]["assignee"]["id"] == me["id"]
    work = client.get("/api/work/me").json()
    assert any(o["id"] == ob["id"] and o["patient_name"] for o in work["assigned"])


def test_med_rec_goes_to_clinicians_only(client):
    data = client.post("/api/patients", json={"name": "Rosa Delgado", "discharge_date": demo_today().isoformat()}).json()
    med_rec = next(o for o in data["obligations"] if o["category"] == "medication_reconciliation")
    users = _users(client)
    refused = client.post(f"/api/obligations/{med_rec['id']}/assign", json={"user_id": users["coordinator"]["id"]})
    assert refused.status_code == 422
    ok = client.post(f"/api/obligations/{med_rec['id']}/assign", json={"user_id": users["clinician"]["id"]})
    assert ok.status_code == 200


def test_unassigned_clinical_work_shows_for_clinicians(client):
    client.post("/api/patients", json={"name": "Rosa Delgado", "risk": "High", "discharge_date": demo_today().isoformat()})
    cred = client.get("/api/auth/demo").json()["clinician"]
    token = client.post("/api/auth/login", json={"email": cred["email"], "password": cred["password"]}).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    work = client.get("/api/work/me").json()
    assert work["role"] == "clinician"
    assert any(o["category"] == "medication_reconciliation" for o in work["unassigned"])
    assert all(o["category"] == "medication_reconciliation" for o in work["unassigned"])
