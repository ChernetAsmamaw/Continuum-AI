"""Editable patient record, duplicate check at intake, voice identity by DOB."""

from __future__ import annotations

from app.workflow import voice_identify

ROSA = {"name": "Rosa Delgado", "birth_date": "1961-04-12", "phone": "+1 416 555 0144", "discharge_date": "2026-11-10"}


def test_intake_stores_dob_and_derives_age(client):
    patient = client.post("/api/patients", json={**ROSA, "age": 20}).json()["patient"]
    assert patient["birth_date"] == "1961-04-12"
    assert patient["age"] == 65  # from the DOB, not the typed age


def test_same_name_and_dob_is_flagged_before_creating(client):
    assert client.post("/api/patients", json=ROSA).status_code == 200
    again = client.post("/api/patients", json={**ROSA, "phone": ""})
    assert again.status_code == 409
    detail = again.json()["detail"]
    assert detail["candidates"][0]["name"] == "Rosa Delgado"
    assert "same name and date of birth" in detail["candidates"][0]["match_reasons"]
    confirmed = client.post("/api/patients", json={**ROSA, "confirm_new": True})
    assert confirmed.status_code == 200


def test_same_name_different_dob_is_not_a_duplicate(client):
    client.post("/api/patients", json=ROSA)
    other = client.post("/api/patients", json={**ROSA, "birth_date": "1990-01-01"})
    assert other.status_code == 200


def test_edit_patient_is_audited_with_before_and_after(client):
    pid = client.post("/api/patients", json=ROSA).json()["patient"]["id"]
    response = client.patch(
        f"/api/patients/{pid}",
        json={
            "phone": "+1 416 555 0199",
            "caregiver": {"name": "Luis Delgado", "relationship": "son", "phone": "+1 416 555 0123", "may_discuss_care": True},
            "preferences": {"consent_to_automated_calls": True, "interpreter_needed": True, "language": "Spanish"},
        },
    )
    assert response.status_code == 200, response.text
    patient = response.json()["patient"]
    assert patient["phone"] == "+1 416 555 0199"
    assert patient["caregiver"] == "Luis Delgado (son)"
    assert patient["caregiver_contact"]["may_discuss_care"] is True
    assert patient["preferences"]["consent_to_automated_calls"] is True
    entry = next(e for e in client.get(f"/api/agent/audit?patient_id={pid}").json()["audit"] if e["action"] == "Updated patient record")
    assert entry["before"]["phone"] == "+1 416 555 0144"
    assert entry["after"]["phone"] == "+1 416 555 0199"
    assert entry["actor"].endswith("(care coordinator)")


def test_edit_that_collides_with_another_patient_is_refused(client):
    client.post("/api/patients", json=ROSA)
    other = client.post("/api/patients", json={"name": "Rosa Delgado", "birth_date": "1990-01-01"}).json()["patient"]
    clash = client.patch(f"/api/patients/{other['id']}", json={"birth_date": "1961-04-12"})
    assert clash.status_code == 409


def test_voice_asks_for_dob_when_one_is_on_file(client):
    client.post("/api/patients", json=ROSA)
    first = voice_identify(given_name="Rosa", family_name="Delgado")
    assert first["count"] == 1
    assert first["matches"][0]["birth_date_verified"] is False
    assert "date of birth" in first["guidance"]

    verified = voice_identify(given_name="Rosa", family_name="Delgado", birth_date="1961-04-12")
    assert verified["matches"][0]["birth_date_verified"] is True
    assert "verified" in verified["guidance"]
    assert "1961" not in str(verified)  # never echoes the DOB

    wrong = voice_identify(given_name="Rosa", family_name="Delgado", birth_date="1961-04-13")
    assert wrong["count"] == 0
