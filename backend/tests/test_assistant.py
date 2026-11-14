import json

from app import assistant
from app.db import demo_today

USER = {"id": "u_test", "name": "Jordan Davis", "role": "coordinator"}
MICHAEL = "pat_michael"


def _tools():
    return {t.name: t for t in assistant.build_tools(USER, demo_today())}


def test_read_tools_return_structured_results(client):
    tools = _tools()
    queue = json.loads(tools["needs_you"].invoke({"limit": 5}))
    assert {"followups", "approvals", "unconfirmed_actions", "counts"} <= set(queue)
    pts = json.loads(tools["search_patients"].invoke({"query": "michael"}))["patients"]
    assert [p["id"] for p in pts] == [MICHAEL]
    summary = json.loads(tools["patient_summary"].invoke({"patient": "Michael Chen"}))
    assert summary["patient"]["id"] == MICHAEL and summary["followups"]
    found = json.loads(tools["find_followups"].invoke({"keyword": "cardiology"}))
    assert found["followups"] and all("ardiolog" in f["description"] for f in found["followups"])
    ob = summary["followups"][0]["id"]
    detail = json.loads(tools["followup_detail"].invoke({"obligation_id": ob}))
    assert detail["followup"]["id"] == ob and "evidence" in detail


def test_navigation_and_proposals_change_nothing(client):
    from app.db import get_db

    tools = _tools()
    nav = json.loads(tools["open_page"].invoke({"page": "call_room", "patient": "Michael"}))
    assert nav["navigate"] == f"/patients/{MICHAEL}/call"
    db = get_db()
    before = (db.approvals.count_documents({}), db.call_sessions.count_documents({}), db.obligations.count_documents({"assignee": {"$ne": None}}))
    ob = db.obligations.find_one({"patient_id": MICHAEL})["id"]
    p = json.loads(tools["propose_contact_log"].invoke({"patient": "Michael", "outcome": "no_answer"}))["proposal"]
    assert p["path"] == f"/api/patients/{MICHAEL}/contacts" and p["method"] == "POST"
    a = json.loads(tools["propose_assign"].invoke({"obligation_id": ob, "person": "Jordan"}))
    assert "proposal" in a or "error" in a
    assert (db.approvals.count_documents({}), db.call_sessions.count_documents({}), db.obligations.count_documents({"assignee": {"$ne": None}})) == before


def test_unknown_patient_is_an_error_not_a_guess(client):
    out = json.loads(_tools()["patient_summary"].invoke({"patient": "Nobody Here"}))
    assert "error" in out


def test_chat_endpoint_requires_sign_in(client):
    r = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": ""})
    assert r.status_code == 401
