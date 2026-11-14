"""Continuum assistant: answers questions about the caseload and drives the console.

A tool-calling agent over the same repository functions the console uses. It
has three kinds of tools:

* read tools: look things up and return structured results the console
  renders as cards (patients, follow-ups, calls, actions, jobs);
* ``open_page``: asks the console to navigate;
* ``propose_*``: suggest an action. Nothing is changed here: the console shows
  a card with Confirm, and the click runs the normal endpoint with the
  signed-in user's permissions and audit attribution.

Replies stream to the console as server-sent events (see ``stream``).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Iterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.tools import tool

from . import calls as calls_mod
from . import ledger
from . import repository as repo
from .config import settings
from .db import demo_today, get_db

log = logging.getLogger("continuum.assistant")

MAX_TURNS = 12

SYSTEM = """You are Continuum's assistant inside the care-coordination console. You help
care coordinators and clinicians find information about their post-discharge
patients and get work done quickly. Today is {today}. The user is {user} ({role}).
{context}

How to work:
- Use the tools to look things up. Never invent patients, dates, statuses or
  results. If a tool returns nothing, say so plainly.
- Keep answers short: one or two sentences, then let the tool's cards show the
  detail. Do not repeat every field the card already shows.
- When the user asks to go somewhere ("open", "show me", "take me to"), call
  open_page.
- When the user asks you to do something that changes data (call a patient,
  approve or reject, assign, log a contact), call the matching propose_ tool.
  You cannot make changes yourself: say the action is ready for them to
  confirm. Never say it has been done.
- Words like "this patient" or "him/her" mean the patient in the current page
  context, if there is one.
- You coordinate care; you never give clinical advice (diagnosis, medication,
  dosing, symptoms). If asked, say a clinician should answer and offer to
  find or escalate the relevant follow-up.
- Use each follow-up's real status word. Only call something "overdue" when
  its status is overdue. "patient_reported" means the patient says it's done
  but there's no proof yet; "conflict" means two dates disagree. Never group
  these under "overdue"."""


def _patient_ref(db, ref: str) -> dict | None:
    """Find a patient by id, MRN or (part of) name."""
    ref = (ref or "").strip()
    if not ref:
        return None
    p = repo.get_patient(db, ref) or db.patients.find_one({"mrn": ref}, {"_id": 0})
    if p:
        return p
    needle = ref.lower()
    for doc in db.patients.find({}, {"_id": 0}):
        if needle in (doc.get("name") or "").lower():
            return doc
    return None


def _ob_row(o: dict, patient_name: str | None = None) -> dict:
    return {
        "id": o["id"], "patient_id": o.get("patient_id"), "patient_name": patient_name,
        "description": o.get("description"), "status": o.get("status"), "due_date": o.get("due_date"),
        "confirmed_date": o.get("confirmed_date"), "days_overdue": o.get("days_overdue") or 0,
        "owner": o.get("owner"), "assignee": (o.get("assignee") or {}).get("name"),
        "latest_evidence": o.get("latest_evidence"), "next_action": o.get("next_action"),
    }


def build_tools(user: dict, today: date):
    db = get_db()
    role = user.get("role") or "coordinator"

    @tool
    def needs_you(limit: int = 10) -> str:
        """The items that need a person right now, most urgent first: overdue follow-ups,
        things to verify, conflicts, pending approvals, and actions that were sent but never
        confirmed. Use for "what needs me", "what's urgent", "my queue"."""
        dash = repo.dashboard(db, today, audience=role)
        names = {p["id"]: p["name"] for p in dash["patients"]}
        obligations = [repo.present_obligation(d, today) for d in db.obligations.find({}, {"_id": 0})]
        urgent = [o for o in obligations if o.get("is_open") and o.get("status") in {"overdue", "conflict", "escalated", "patient_reported"}]
        rank = {"overdue": 0, "escalated": 1, "conflict": 2, "patient_reported": 3}
        urgent.sort(key=lambda o: (rank.get(o["status"], 9), -(o.get("days_overdue") or 0)))
        approvals = [{"id": a["id"], "patient_id": a.get("patient_id"), "patient_name": names.get(a.get("patient_id")),
                      "title": a.get("title"), "kind": a.get("kind"), "created_at": a.get("created_at")}
                     for a in dash["pending_approvals"][:limit]]
        unconfirmed = [{"id": a["id"], "patient_id": a.get("patient_id"), "patient_name": names.get(a.get("patient_id")),
                        "summary": a.get("summary"), "status": a.get("status")}
                       for a in ledger.list_actions(db, status="unconfirmed,unknown", limit=limit)]
        return json.dumps({"followups": [_ob_row(o, names.get(o.get("patient_id"))) for o in urgent[:limit]],
                           "approvals": approvals, "unconfirmed_actions": unconfirmed,
                           "counts": {"followups": len(urgent), "approvals": len(dash["pending_approvals"]),
                                      "unconfirmed_actions": len(unconfirmed)}}, default=str)

    @tool
    def search_patients(query: str = "", filter: str = "all") -> str:
        """List or search patients in the 30-day window. query matches name, MRN or condition.
        filter: all | overdue | needs_you | conflict. Returns days since discharge, the next
        deadline and counts per patient."""
        rows = repo.list_patients(db, today)
        q = (query or "").lower().strip()
        out = []
        for p in rows:
            if q and q not in " ".join([p.get("name", ""), p.get("mrn", ""), p.get("condition", "")]).lower():
                continue
            if filter == "overdue" and not p["overdue_count"]:
                continue
            if filter == "conflict" and not p["conflict_count"]:
                continue
            needs = p["overdue_count"] + p["conflict_count"] + p["pending_verification"] + p["escalation_count"]
            if filter == "needs_you" and not needs:
                continue
            out.append({"id": p["id"], "name": p["name"], "mrn": p.get("mrn"), "condition": p.get("condition"),
                        "days_since_discharge": p.get("days_since_discharge"), "next_deadline": p.get("next_deadline"),
                        "overdue": p["overdue_count"], "needs_you": needs, "open": p["open_count"],
                        "coordinator": p.get("coordinator")})
        return json.dumps({"patients": out}, default=str)

    @tool
    def find_followups(keyword: str = "", status: str = "", overdue_only: bool = False) -> str:
        """Search follow-ups across ALL patients in one call. keyword matches the follow-up's
        description or type (e.g. bloodwork, cardiology, home care). status filters, comma-separated:
        pending, scheduled, patient_reported, verified, overdue, conflict, escalated. Use for
        questions like "who hasn't had bloodwork verified" or "which home-care visits are late"."""
        names = {p["id"]: p["name"] for p in db.patients.find({}, {"_id": 0, "id": 1, "name": 1})}
        k = (keyword or "").lower().replace("-", " ").strip()
        wanted = {x.strip() for x in status.split(",") if x.strip()}
        rows = []
        for d in db.obligations.find({}, {"_id": 0}):
            o = repo.present_obligation(d, today)
            text = f"{o.get('description', '')} {o.get('category', '')}".lower().replace("_", " ").replace("-", " ")
            if k and not all(w in text for w in k.split()):
                continue
            if wanted and o.get("status") not in wanted:
                continue
            if overdue_only and o.get("status") != "overdue":
                continue
            if o.get("patient_id") not in names:
                continue
            rows.append(_ob_row(o, names[o["patient_id"]]))
        rows.sort(key=lambda r: (r["status"] != "overdue", r.get("due_date") or "9999"))
        return json.dumps({"followups": rows[:25], "total": len(rows)}, default=str)

    @tool
    def patient_summary(patient: str) -> str:
        """Everything about one patient: details, contact preferences, open and closed
        follow-ups with status and dates, and the latest calls. `patient` is a name, MRN or id."""
        p = _patient_ref(db, patient)
        if not p:
            return json.dumps({"error": f"No patient matches '{patient}'."})
        j = repo.journey(db, p["id"], today)
        obs = [_ob_row(o, p["name"]) for o in j.get("obligations", [])]
        calls = calls_mod.list_sessions(db, p["id"])[:3]
        return json.dumps({
            "patient": {"id": p["id"], "name": p["name"], "age": p.get("age"), "mrn": p.get("mrn"),
                        "condition": p.get("condition"), "risk": p.get("risk"), "phone": p.get("phone"),
                        "preferences": p.get("preferences"), "caregiver": p.get("caregiver")},
            "followups": obs,
            "recent_calls": [{"id": c["id"], "mode": c.get("mode"), "status": c.get("status"),
                              "created_at": c.get("created_at"),
                              "changes": [e.get("title") for e in (c.get("effects") or [])][:6]} for c in calls],
        }, default=str)

    @tool
    def followup_detail(obligation_id: str) -> str:
        """One follow-up in full: status, dates, owner, its evidence (what proves it) and its
        history of changes. Use when asked why something is in its state."""
        o = repo.get_obligation(db, obligation_id)
        if not o:
            return json.dumps({"error": "No such follow-up."})
        o = repo.present_obligation(o, today)
        p = repo.get_patient(db, o["patient_id"]) or {}
        ev = [{"kind": e.get("kind"), "summary": e.get("summary"), "verified": e.get("verified"),
               "at": e.get("created_at") or e.get("occurred_at"), "source": e.get("source")}
              for e in repo.get_evidence(db, obligation_id)][-8:]
        hist = (o.get("history") or [])[-8:]
        return json.dumps({"followup": _ob_row(o, p.get("name")), "evidence": ev, "history": hist,
                           "conflicts": o.get("conflicts")}, default=str)

    @tool
    def call_details(patient: str) -> str:
        """The most recent call with a patient: transcript lines, the tools the voice agent used,
        and what changed in the record. `patient` is a name, MRN or id."""
        p = _patient_ref(db, patient)
        if not p:
            return json.dumps({"error": f"No patient matches '{patient}'."})
        sessions = calls_mod.list_sessions(db, p["id"])
        if not sessions:
            return json.dumps({"patient_name": p["name"], "call": None})
        s = calls_mod.get_session(db, sessions[0]["id"]) or sessions[0]
        lines = [{"role": e.get("role"), "text": e.get("text")} for e in s.get("events", []) if e.get("kind") == "transcript"]
        tools_used = [{"tool": e.get("tool"), "summary": e.get("text")} for e in s.get("events", []) if e.get("kind") == "tool"]
        return json.dumps({"patient_id": p["id"], "patient_name": p["name"], "call": {
            "id": s["id"], "mode": s.get("mode"), "status": s.get("status"), "created_at": s.get("created_at"),
            "transcript": lines[-24:], "tools": tools_used,
            "changes": [{"type": e.get("type"), "title": e.get("title"), "to": e.get("to")} for e in s.get("effects") or []]}},
            default=str)

    @tool
    def action_ledger(status: str = "", patient: str = "") -> str:
        """Outbound actions (calls, texts, pages) and whether they took effect:
        intended, sent, confirmed, unconfirmed, failed or unknown. status filters
        (comma-separated); patient narrows to one patient."""
        pid = None
        if patient:
            p = _patient_ref(db, patient)
            pid = p["id"] if p else None
        acts = ledger.list_actions(db, patient_id=pid, status=status or None, limit=20)
        return json.dumps({"summary": ledger.summary(db), "actions": [
            {"id": a["id"], "kind": a.get("kind"), "status": a.get("status"), "summary": a.get("summary"),
             "patient_id": a.get("patient_id"), "updated_at": a.get("updated_at")} for a in acts]}, default=str)

    @tool
    def ingestion_jobs(limit: int = 6) -> str:
        """Recent discharges imported from the hospital EHR: the job, its steps, status and
        how many follow-ups it created."""
        jobs = list(db.ingest_jobs.find({}, {"_id": 0}).sort("created_at", -1).limit(limit))
        return json.dumps({"jobs": [{"id": j["id"], "status": j.get("status"), "patient_id": j.get("patient_id"),
                                     "attempts": j.get("attempts"), "created_at": j.get("created_at"),
                                     "steps": {k: (v or {}).get("status") for k, v in (j.get("steps") or {}).items()},
                                     "result": {k: (j.get("result") or {}).get(k) for k in ("followups", "created", "message")}}
                                    for j in jobs]}, default=str)

    @tool
    def open_page(page: str, patient: str = "") -> str:
        """Navigate the console. page: needs_you | patients | patient | call_room | approvals |
        actions | ingestion | audit | work. For patient or call_room give the patient
        (name, MRN or id)."""
        routes = {"needs_you": "/console", "patients": "/patients", "approvals": "/approvals", "actions": "/actions",
                  "ingestion": "/ingestion", "audit": "/audit", "work": "/work"}
        if page in {"patient", "call_room"}:
            p = _patient_ref(db, patient)
            if not p:
                return json.dumps({"error": f"No patient matches '{patient}'."})
            path = f"/patients/{p['id']}" + ("/call" if page == "call_room" else "")
            return json.dumps({"navigate": path, "label": f"{p['name']}" + (" · call room" if page == "call_room" else "")})
        if page not in routes:
            return json.dumps({"error": f"Unknown page '{page}'."})
        return json.dumps({"navigate": routes[page], "label": page.replace("_", " ").capitalize()})

    @tool
    def propose_call(patient: str, mode: str = "phone") -> str:
        """Prepare a check-in call for the user to confirm. mode: phone (rings the patient's
        number on file) or browser (talk through this computer). Does not place the call."""
        p = _patient_ref(db, patient)
        if not p:
            return json.dumps({"error": f"No patient matches '{patient}'."})
        modes = calls_mod.available_modes()
        if not modes.get(mode, {}).get("available"):
            return json.dumps({"error": f"{mode.capitalize()} calls are not available: {modes.get(mode, {}).get('why')}"})
        return json.dumps({"proposal": {
            "kind": "call", "title": f"Call {p['name']}", "detail": f"{mode.capitalize()} check-in call",
            "method": "POST", "path": "/api/calls", "body": {"patient_id": p["id"], "mode": mode},
            "then_navigate": f"/patients/{p['id']}/call", "confirm_label": "Start call"}})

    @tool
    def propose_decision(approval_id: str, approve: bool, note: str = "") -> str:
        """Prepare approving or rejecting a pending approval for the user to confirm."""
        a = repo.get_approval(db, approval_id)
        if not a or a.get("status") != "pending":
            return json.dumps({"error": "That approval is not pending."})
        return json.dumps({"proposal": {
            "kind": "decision", "title": ("Approve: " if approve else "Reject: ") + (a.get("title") or "approval"),
            "detail": a.get("rationale") or "", "method": "POST", "path": f"/api/agent/approvals/{approval_id}/resolve",
            "body": {"approve": approve, "note": note or None}, "confirm_label": "Approve" if approve else "Reject"}})

    @tool
    def propose_assign(obligation_id: str, person: str) -> str:
        """Prepare assigning a follow-up to a team member (by name) for the user to confirm."""
        o = repo.get_obligation(db, obligation_id)
        if not o:
            return json.dumps({"error": "No such follow-up."})
        target = next((u for u in db.users.find({}, {"_id": 0, "id": 1, "name": 1, "role": 1})
                       if person.lower() in (u.get("name") or "").lower()), None)
        if not target:
            names = [u.get("name") for u in db.users.find({}, {"_id": 0, "name": 1})]
            return json.dumps({"error": f"No team member matches '{person}'. Team: {', '.join(n for n in names if n)}."})
        return json.dumps({"proposal": {
            "kind": "assign", "title": f"Assign to {target['name']}", "detail": o.get("description"),
            "method": "POST", "path": f"/api/obligations/{obligation_id}/assign", "body": {"user_id": target["id"]},
            "confirm_label": "Assign"}})

    @tool
    def propose_contact_log(patient: str, outcome: str, channel: str = "phone", note: str = "") -> str:
        """Prepare logging a contact attempt the user made (e.g. reached, no_answer,
        left_voicemail) for the user to confirm."""
        p = _patient_ref(db, patient)
        if not p:
            return json.dumps({"error": f"No patient matches '{patient}'."})
        return json.dumps({"proposal": {
            "kind": "contact", "title": f"Log contact with {p['name']}", "detail": f"{outcome.replace('_', ' ')} by {channel}" + (f": {note}" if note else ""),
            "method": "POST", "path": f"/api/patients/{p['id']}/contacts",
            "body": {"outcome": outcome, "channel": channel, "note": note or None}, "confirm_label": "Log it"}})

    return [needs_you, search_patients, find_followups, patient_summary, followup_detail, call_details, action_ledger,
            ingestion_jobs, open_page, propose_call, propose_decision, propose_assign, propose_contact_log]


def _model():
    import warnings

    from langchain.chat_models import init_chat_model

    warnings.filterwarnings("ignore", message=".*fixed sampling defaults.*")

    from .agent.llm import _env_key, _provider_kwargs, chat_configured, chat_model_name, chat_provider

    if not chat_configured():
        return None
    provider = chat_provider()
    name = settings.assistant_model or chat_model_name()
    kwargs = _provider_kwargs(provider, settings.llm_api_key or _env_key(provider), settings.llm_base_url)
    return init_chat_model(name, model_provider=provider, temperature=0, max_retries=1, timeout=40, **kwargs)


def _context_line(db, context: dict | None) -> str:
    context = context or {}
    parts = []
    if context.get("path"):
        parts.append(f"The user is on the page {context['path']}.")
    pid = context.get("patient_id")
    if pid:
        p = repo.get_patient(db, pid)
        if p:
            parts.append(f"Current patient: {p['name']} (id {p['id']}, MRN {p.get('mrn')}).")
    return " ".join(parts)


def _history(messages: list[dict]) -> list:
    out = []
    for m in messages[-MAX_TURNS:]:
        text = (m.get("content") or "").strip()
        if not text:
            continue
        out.append(HumanMessage(text) if m.get("role") == "user" else AIMessage(text))
    return out


def _text(chunk) -> str:
    content = chunk.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def stream(messages: list[dict], context: dict | None, user: dict) -> Iterator[dict]:
    """Yield events: {"type": "text", "text"} | {"type": "tool", "id", "name", "args"} |
    {"type": "result", "id", "name", "data"} | {"type": "error", "message"} | {"type": "done"}."""
    from langchain.agents import create_agent

    model = _model()
    if model is None:
        yield {"type": "text", "text": "The assistant needs an LLM key (LLM_PROVIDER and LLM_API_KEY)."}
        yield {"type": "done"}
        return
    db = get_db()
    today = demo_today()
    system = SYSTEM.format(today=today.isoformat(), user=user.get("name", "a user"), role=user.get("role", "coordinator"),
                           context=_context_line(db, context))
    agent = create_agent(model, build_tools(user, today), system_prompt=system)
    announced: set[str] = set()
    try:
        for chunk, _meta in agent.stream({"messages": _history(messages)}, stream_mode="messages",
                                         config={"recursion_limit": 24, "run_name": "Continuum assistant",
                                                 "metadata": {"user": user.get("id"), "path": (context or {}).get("path")}}):
            if isinstance(chunk, ToolMessage):
                try:
                    data = json.loads(chunk.content) if isinstance(chunk.content, str) else chunk.content
                except (TypeError, ValueError):
                    data = {"text": str(chunk.content)}
                yield {"type": "result", "id": chunk.tool_call_id, "name": chunk.name, "data": data}
                continue
            if isinstance(chunk, (AIMessageChunk, AIMessage)):
                for tc in chunk.tool_call_chunks if isinstance(chunk, AIMessageChunk) else chunk.tool_calls:
                    tid, name = tc.get("id"), tc.get("name")
                    if tid and name and tid not in announced:
                        announced.add(tid)
                        yield {"type": "tool", "id": tid, "name": name}
                text = _text(chunk)
                if text:
                    yield {"type": "text", "text": text}
    except Exception as exc:  # pragma: no cover - network dependent
        log.warning("Assistant run failed: %s", exc)
        yield {"type": "error", "message": "The assistant couldn't finish that. Try again in a moment."}
    yield {"type": "done"}
