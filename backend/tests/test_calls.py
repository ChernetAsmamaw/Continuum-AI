"""Call Room: brief from memory, live events, what changed, ledger, simulated calls."""

from __future__ import annotations

import time

import pytest

from app import calls

MICHAEL = "pat_michael"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(calls, "SIM_DELAY", 0)


def _wait(client, session_id, status="ended", timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/calls/{session_id}").json()["session"]
        if s["status"] in {status, "failed"}:
            return s
        time.sleep(0.2)
    raise AssertionError("call did not finish")


def test_modes_without_keys(client, monkeypatch):
    from app.config import settings

    for name in ("elevenlabs_api_key", "elevenlabs_agent_id", "elevenlabs_phone_number_id"):
        monkeypatch.setattr(settings, name, "")
    modes = client.get("/api/calls/modes").json()
    assert modes["simulated"]["available"] is True
    assert modes["browser"]["available"] is False and "ELEVENLABS_AGENT_ID" in modes["browser"]["why"]
    assert modes["phone"]["available"] is False
    r = client.post("/api/calls", json={"patient_id": MICHAEL, "mode": "phone"})
    assert r.status_code == 422


def test_brief_is_ordered_and_explains_why(client):
    client.post("/api/demo/day/10", json={"day": 10})  # creates a date conflict for Michael
    brief = client.get(f"/api/calls/brief/{MICHAEL}").json()
    keys = [a["key"] for a in brief["agenda"]]
    assert keys[:2] == ["disclosure", "identity"] and keys[-1] == "safety"
    followups = [a for a in brief["agenda"] if a["kind"] == "followup"]
    assert followups and all(a["why"] for a in followups)
    assert "Two dates on record" in followups[0]["why"]  # conflicts first


def test_simulated_call_drives_real_tools_and_shows_what_changed(client, db):
    client.post("/api/demo/day/0", json={"day": 0})
    before_pending = db.approvals.count_documents({"patient_id": MICHAEL, "status": "pending"})
    session = client.post("/api/calls", json={"patient_id": MICHAEL, "mode": "simulated"}).json()["session"]
    done = _wait(client, session["id"])
    assert done["status"] == "ended", done.get("error")
    kinds = [e["kind"] for e in done["events"]]
    assert kinds.count("transcript") >= 8 and "tool" in kinds
    tools = [e for e in done["events"] if e["kind"] == "tool"]
    assert {"identify", "report", "callback"} <= {t["tool"] for t in tools}
    # A patient saying "I did the bloodwork" is recorded as unverified, never verified.
    report = next(t for t in tools if t["tool"] == "report")
    changes = [e for e in report["effects"] if e["type"] == "status_changed"]
    assert changes and all(not c["verified"] for c in changes)
    assert any(e["type"] == "approval_queued" for t in tools for e in t["effects"]) or \
        db.approvals.count_documents({"patient_id": MICHAEL, "status": "pending"}) > before_pending
    assert done["effects"], "summary of what the call changed"
    action = db.actions.find_one({"key": f"call:{session['id']}"})
    assert [h["status"] for h in action["history"]] == ["intended", "sent", "confirmed"]
    # Only one live call per patient; a new one can start after.
    assert calls.active_session(db, MICHAEL) is None


def test_live_tool_call_from_elevenlabs_lands_on_the_call(client, db, monkeypatch):
    monkeypatch.setattr(calls, "available_modes", lambda: {m: {"available": True, "why": None} for m in calls.MODES})
    session = client.post("/api/calls", json={"patient_id": MICHAEL, "mode": "browser"}).json()["session"]
    client.post(f"/api/calls/{session['id']}/connected", json={"conversation_id": "conv-browser-1"})
    client.post(f"/api/calls/{session['id']}/transcript", json={"role": "patient", "text": "Should I double my water pill?"})
    # ElevenLabs server tool → Continuum voice endpoint, carrying the call id as a dynamic variable.
    r = client.post("/api/voice/escalate", json={"patient_id": MICHAEL, "reason": "Asks about doubling diuretic", "call_session_id": session["id"]})
    assert r.status_code == 200
    s = client.get(f"/api/calls/{session['id']}").json()["session"]
    tool = next(e for e in s["events"] if e["kind"] == "tool")
    assert tool["tool"] == "escalate" and any(e["type"] == "approval_queued" for e in tool["effects"])
    ended = client.post(f"/api/calls/{session['id']}/end").json()["session"]
    assert ended["status"] == "ended"
    assert db.actions.find_one({"key": f"call:{session['id']}"})["status"] == "confirmed"


def test_call_with_no_conversation_fails_its_ledger_entry(client, db, monkeypatch):
    monkeypatch.setattr(calls, "available_modes", lambda: {m: {"available": True, "why": None} for m in calls.MODES})
    session = client.post("/api/calls", json={"patient_id": MICHAEL, "mode": "browser"}).json()["session"]
    client.post(f"/api/calls/{session['id']}/end")
    assert db.actions.find_one({"key": f"call:{session['id']}"})["status"] == "failed"
