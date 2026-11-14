"""Proactive follow-through: the sweep acts without anyone opening the app."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app import scheduler
from app.config import settings
from app.scheduler import run_sweep

MICHAEL = "pat_michael"


def _future(days: int) -> tuple[date, datetime]:
    d = date.today() + timedelta(days=days)
    return d, datetime.combine(d, datetime.max.time(), tzinfo=timezone.utc)


def _daytime(d: date | None = None) -> datetime:
    """3pm in the calling-hours timezone: always inside the automated-call window."""
    from zoneinfo import ZoneInfo

    d = d or date.today()
    return datetime(d.year, d.month, d.day, 15, tzinfo=ZoneInfo(settings.call_timezone)).astimezone(timezone.utc)


def _scheduler_tasks(client) -> list[dict]:
    approvals = client.get("/api/agent/approvals", params={"patient_id": MICHAEL}).json()["approvals"]
    return [a for a in approvals if a.get("source") == "scheduler"]


def test_overdue_items_get_a_checkin_then_an_owner_task_once(client, db):
    client.post("/api/demo/day/0", json={"day": 0})
    today, now = _future(45)

    first = run_sweep(today=today, now=now)
    chased = [a for a in first["actions"] if a["type"] == "overdue_checkin" and a["patient_id"] == MICHAEL]
    escalated = [a for a in first["actions"] if a["type"] == "overdue_escalated"]
    assert len(chased) == 4  # every Day-0 follow-up is past due 45 days later
    assert escalated, "items overdue past the escalation threshold need an owner task"
    assert db.checkins.count_documents({"patient_id": MICHAEL, "created_by": "Continuum scheduler"}) == 4

    # Idempotent: a second sweep the same day must not chase the same items again.
    second = run_sweep(today=today, now=now)
    assert not [a for a in second["actions"] if a["type"] in {"overdue_checkin", "overdue_escalated"}]
    assert db.checkins.count_documents({"patient_id": MICHAEL, "created_by": "Continuum scheduler"}) == 4

    tasks = _scheduler_tasks(client)
    assert any("days overdue" in t["title"] for t in tasks)
    assert all(t["audience"] == "coordinator" for t in tasks)


def test_due_checkin_becomes_coordinator_call_without_telephony(client, db):
    client.post("/api/demo/day/0", json={"day": 0})
    today, now = _future(45)
    run_sweep(today=today, now=now)  # schedules check-ins for "today"
    result = run_sweep(today=today, now=now)  # dispatches them

    dispatched = [a for a in result["actions"] if a["type"] == "checkin" and a["patient_id"] == MICHAEL]
    assert dispatched and all(a["status"] == "awaiting_manual_call" for a in dispatched)
    assert any(t["title"].startswith("Call Michael") for t in _scheduler_tasks(client))
    events = client.get(f"/api/patients/{MICHAEL}/journey").json()["events"]
    assert any(e["title"] == "Check-in due — coordinator call needed" for e in events)


def test_future_checkins_wait_and_unclear_ones_are_flagged(client, db):
    from app import repository as repo

    future = repo.schedule_checkin(
        db, {"patient_id": MICHAEL, "scheduled_for": (date.today() + timedelta(days=3)).isoformat(), "reason": "later"}
    )
    unclear = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": "whenever works", "reason": "?"})
    loose = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": "tomorrow afternoon", "reason": "cb"})

    run_sweep(today=date.today(), now=datetime.now(timezone.utc))
    assert db.checkins.find_one({"id": future["id"]})["status"] == "scheduled"
    assert db.checkins.find_one({"id": unclear["id"]})["status"] == "needs_review"
    assert db.checkins.find_one({"id": loose["id"]})["status"] == "scheduled"

    run_sweep(today=date.today() + timedelta(days=1), now=datetime.now(timezone.utc) + timedelta(days=1))
    assert db.checkins.find_one({"id": loose["id"]})["status"] == "awaiting_manual_call"


def test_no_consent_means_no_automated_call(client, db):
    from app import repository as repo

    db.patients.update_one({"id": MICHAEL}, {"$set": {"preferences.consent_to_automated_calls": False}})
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=datetime.now(timezone.utc))
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "skipped_no_consent"
    assert any(t["title"].startswith("Contact Michael") for t in _scheduler_tasks(client))


def test_stale_patient_report_requests_proof(client, db):
    client.post("/api/demo/day/3", json={"day": 3})
    today, now = _future(settings.stale_report_after_days + 1)
    result = run_sweep(today=today, now=now)
    stale = [a for a in result["actions"] if a["type"] == "stale_report"]
    assert len(stale) == 1
    assert any(t["title"].startswith("Request proof: Family physician") for t in _scheduler_tasks(client))
    assert not [a for a in run_sweep(today=today, now=now)["actions"] if a["type"] == "stale_report"]


def test_outbound_call_placed_when_telephony_configured(client, db, monkeypatch):
    from app import repository as repo

    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "elevenlabs_agent_id", "agent_test")
    monkeypatch.setattr(settings, "elevenlabs_phone_number_id", "phnum_test")
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142"}})
    sent = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "message": "ok", "conversation_id": "conv_test"}

    def fake_post(url, json, headers, timeout):
        sent.update(url=url, body=json, headers=headers)
        return FakeResponse()

    monkeypatch.setattr(scheduler.httpx, "post", fake_post)
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_daytime())

    assert db.checkins.find_one({"id": chk["id"]})["status"] == "dispatched"
    assert sent["body"]["to_number"] == "+14165550142"
    assert sent["body"]["conversation_initiation_client_data"]["dynamic_variables"]["patient_id"] == MICHAEL
    assert db.voice_calls.find_one({"elevenlabs_conversation_id": "conv_test"})


def test_synthetic_numbers_are_never_dialled(client, db, monkeypatch):
    from app import repository as repo

    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    monkeypatch.setattr(settings, "elevenlabs_agent_id", "a")
    monkeypatch.setattr(settings, "elevenlabs_phone_number_id", "p")
    monkeypatch.setattr(scheduler.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("dialled")))
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_daytime())
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "awaiting_manual_call"


def test_sweep_endpoint_is_coordinator_only(client):
    assert client.post("/api/admin/sweep").status_code == 200
    assert client.get("/api/admin/sweep").json()["last_run"] is not None
