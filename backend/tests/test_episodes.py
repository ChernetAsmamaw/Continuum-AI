"""Readmission opens a new episode and carries open loops; closing guards them."""

from __future__ import annotations

from app.db import demo_today


def _patient(client):
    data = client.post(
        "/api/patients",
        json={"name": "Rosa Delgado", "discharge_date": "2026-10-20",
              "obligations": [{"description": "Cardiology follow-up", "due_date": "2026-11-30"}]},
    ).json()
    return data["patient"]["id"]


def test_readmission_carries_open_follow_ups(client, db):
    pid = _patient(client)
    old_episode = client.get(f"/api/patients/{pid}/journey").json()["episode"]["id"]
    result = client.post(f"/api/patients/{pid}/episodes", json={"discharge_date": demo_today().isoformat(), "reason": "Heart failure, readmitted"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["previous_episode_id"] == old_episode
    assert body["carried_over"]
    journey = client.get(f"/api/patients/{pid}/journey").json()
    assert journey["episode"]["id"] == body["episode"]["id"]
    carried = next(o for o in journey["obligations"] if o["description"] == "Cardiology follow-up")
    assert carried["carried_over_from"] == old_episode
    assert db.care_episodes.find_one({"id": old_episode})["closed_reason"] == "readmitted"
    assert journey["tcm"]["discharge_date"] == demo_today().isoformat()


def test_close_refuses_with_open_loops_then_cancels_with_reason(client):
    pid = _patient(client)
    refused = client.post(f"/api/patients/{pid}/episodes/close", json={"reason": "window_complete"})
    assert refused.status_code == 409
    assert refused.json()["detail"]["candidates"]
    closed = client.post(f"/api/patients/{pid}/episodes/close", json={"reason": "transferred", "note": "Moved to LTC", "close_open": True})
    assert closed.status_code == 200, closed.text
    journey = client.get(f"/api/patients/{pid}/journey").json()
    assert journey["episode"] is None
    assert all(o["status"] == "cancelled" for o in journey["obligations"])
