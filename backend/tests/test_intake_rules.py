"""Intake rules from the workflow audit: due dates always set, consent opt-in."""

from __future__ import annotations


def test_follow_up_without_due_date_defaults_to_a_week_after_discharge(client):
    response = client.post(
        "/api/patients",
        json={
            "name": "Rosa Delgado",
            "discharge_date": "2026-11-10",
            "obligations": [{"description": "Family physician review"}],
        },
    )
    assert response.status_code == 200, response.text
    obligation = next(o for o in response.json()["obligations"] if o["description"] == "Family physician review")
    assert obligation["due_date"] == "2026-11-17"


def test_consent_is_not_assumed(client):
    response = client.post("/api/patients", json={"name": "Rosa Delgado"})
    prefs = response.json()["patient"]["preferences"]
    assert prefs["consent_to_automated_calls"] is False
    assert prefs["consent_to_recording"] is False


def test_document_upload_needs_no_patient_id_in_body(client):
    response = client.post(
        "/api/patients/pat_sarah/documents",
        json={"title": "Consult note", "text": "Seen in clinic. Plan: repeat HbA1c in 3 months."},
    )
    assert response.status_code == 200, response.text
