"""LangGraph workflow: ingest → extract → retrieve → validate → propose →
apply → approve → compose.

Every channel (document, chat, ElevenLabs transcript) runs the same graph.
Graph state is checkpointed per patient thread in MongoDB. Sensitive actions
requested within an interaction pause the graph with ``interrupt`` until a
coordinator resolves them.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .. import engine
from .. import repository as repo
from ..db import demo_today, get_db
from ..schemas import ApprovalKind
from ..util import new_id, truncate
from .checkpoint import get_checkpointer
from .extraction import extract as run_extraction
from .llm import message_text, get_chat_model
from .memory import render_context, retrieve_context

log = logging.getLogger("continuum.graph")


class AgentState(TypedDict, total=False):
    patient_id: str
    thread_id: str
    run_id: str
    input_type: str
    input_text: str
    title: str
    source_ref: str
    actor: str
    transcript: list[dict]
    metadata: dict
    event_id: str
    episode_id: str
    anchor: str
    extraction: dict
    retrieval: dict
    proposals: list[dict]
    notes: list[str]
    approval_ids: list[str]
    applied: list[str]
    pending_approvals: list[dict]
    reply_draft: str
    reply: str
    spoken: str
    status: str
    interrupted: bool


EVENT_TITLES = {
    "document": "Discharge summary processed",
    "chat": "Coordinator chat with Continuum",
    "voice_transcript": "Voice check-in completed",
    "voice_report": "Patient voice update",
    "coordinator": "Coordinator update",
}


# ── Nodes ────────────────────────────────────────────────────────────────


def ingest(state: AgentState) -> dict:
    db = get_db()
    patient = repo.get_patient(db, state["patient_id"])
    if not patient:
        raise ValueError(f"Unknown patient {state['patient_id']}")
    episode = repo.get_active_episode(db, state["patient_id"]) or {}
    anchor = (
        episode.get("discharge_date")
        if state["input_type"] == "document" and episode.get("discharge_date")
        else demo_today().isoformat()
    )
    event = repo.add_event(
        db,
        {
            "patient_id": state["patient_id"],
            "episode_id": episode.get("id"),
            "type": "voice_call" if state["input_type"].startswith("voice") else state["input_type"],
            "title": state.get("title") or EVENT_TITLES.get(state["input_type"], "Care interaction"),
            "detail": truncate(state["input_text"], 400),
            "actor": state.get("actor", "Continuum"),
            "source_ref": state.get("source_ref"),
            "transcript": state.get("transcript") or [],
            "metadata": state.get("metadata") or {},
        },
    )
    repo.add_memory(
        db,
        patient_id=state["patient_id"],
        kind="interaction",
        summary=f"{event['title']}: {truncate(state['input_text'], 180)}",
        content=state["input_text"],
        tags=["interaction", state["input_type"]],
        importance=0.5,
        source_event_id=event["id"],
    )
    return {
        "event_id": event["id"],
        "episode_id": episode.get("id"),
        "anchor": anchor,
        "status": "running",
        # Clear any reply/approval state from a previous run on this thread.
        "reply": "",
        "spoken": "",
        "reply_draft": "",
        "applied": [],
        "notes": [],
        "proposals": [],
        "pending_approvals": [],
        "approval_ids": [],
        "interrupted": False,
    }


def extract_node(state: AgentState) -> dict:
    db = get_db()
    patient = repo.get_patient(db, state["patient_id"]) or {}
    existing = repo.list_obligations(db, state["patient_id"], include_closed=True)
    extraction = run_extraction(
        state["input_text"],
        input_type=state["input_type"],
        patient=patient,
        existing_obligations=existing,
        anchor=state.get("anchor"),
    )
    return {"extraction": extraction}


def retrieve_node(state: AgentState) -> dict:
    db = get_db()
    bundle = retrieve_context(
        db,
        state["patient_id"],
        state["input_text"],
        k=6,
        today=demo_today(),
    )
    return {"retrieval": bundle}


def validate_node(state: AgentState) -> dict:
    db = get_db()
    existing = repo.list_obligations(db, state["patient_id"], include_closed=True)
    proposals, notes = engine.validate(
        db,
        extraction=state.get("extraction") or {},
        existing=existing,
        input_type=state["input_type"],
        event_id=state.get("event_id"),
        anchor=state.get("anchor") or demo_today().isoformat(),
        run_id=state["run_id"],
        thread_id=state["thread_id"],
    )
    for p in proposals:
        p["patient_id"] = state["patient_id"]
        p["episode_id"] = state.get("episode_id")
        p["thread_id"] = state["thread_id"]
        p["run_id"] = state["run_id"]
    return {"proposals": proposals, "notes": notes}


def propose_node(state: AgentState) -> dict:
    """Persist approval requests for sensitive proposals and draft a reply."""
    db = get_db()
    approval_ids: list[str] = []
    pending: list[dict] = []
    for proposal in state.get("proposals", []):
        if not proposal.get("requires_approval"):
            continue
        approval = repo.create_approval(
            db,
            {
                "patient_id": state["patient_id"],
                "run_id": state["run_id"],
                "thread_id": state["thread_id"],
                "kind": proposal.get("approval_kind") or ApprovalKind.override.value,
                "mode": "interrupt" if proposal.get("interrupt") else "queued",
                "obligation_id": proposal.get("obligation_id"),
                "title": proposal["title"],
                "rationale": proposal["rationale"],
                "proposed_changes": proposal.get("changes", {}),
                "evidence_summary": proposal.get("evidence_summary"),
                "confidence": proposal.get("confidence", 0.7),
                "proposal": proposal,
            },
        )
        approval_ids.append(approval["id"])
        proposal["approval_id"] = approval["id"]
        pending.append(approval)

    draft = _draft_reply(state, pending)
    return {
        "proposals": state.get("proposals", []),
        "approval_ids": approval_ids,
        "pending_approvals": [
            {
                "id": a["id"],
                "title": a.get("title"),
                "mode": a.get("mode"),
                "kind": a.get("kind"),
            }
            for a in pending
        ],
        "reply_draft": draft,
    }


def apply_auto_node(state: AgentState) -> dict:
    """Apply administrative, non-sensitive changes. Never closes a loop."""
    db = get_db()
    applied: list[str] = []
    for proposal in state.get("proposals", []):
        if proposal.get("requires_approval"):
            continue
        proposal.setdefault("patient_id", state["patient_id"])
        proposal.setdefault("episode_id", state.get("episode_id"))
        try:
            engine.apply_proposal(
                db,
                proposal,
                actor="Continuum",
                thread_id=state["thread_id"],
                run_id=state["run_id"],
            )
            applied.append(proposal["id"])
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Failed to apply proposal %s: %s", proposal.get("id"), exc)
    return {"applied": applied}


def await_approval_node(state: AgentState) -> dict:
    """Pause for sensitive changes requested inside this interaction.

    On resume, ``interrupt`` returns a decision map keyed by approval id.
    """
    props = [
        p
        for p in state.get("proposals", [])
        if p.get("requires_approval") and p.get("interrupt")
    ]
    if not props:
        return {"interrupted": False}

    payload = {
        "type": "approval_required",
        "patient_id": state["patient_id"],
        "run_id": state["run_id"],
        "message": state.get("reply_draft"),
        "approvals": [
            {
                "id": p.get("approval_id"),
                "title": p["title"],
                "rationale": p["rationale"],
                "kind": p.get("approval_kind"),
                "proposed_changes": p.get("changes"),
                "evidence": p.get("evidence_summary"),
                "obligation_id": p.get("obligation_id"),
            }
            for p in props
        ],
    }
    decisions: dict[str, Any] = interrupt(payload) or {}
    db = get_db()
    applied: list[str] = list(state.get("applied") or [])
    for proposal in props:
        approval_id = proposal.get("approval_id") or ""
        decision = decisions.get(approval_id) or decisions.get(proposal["id"]) or {}
        approved = bool(decision.get("approved"))
        if approved:
            engine.apply_proposal(
                db,
                {**proposal, "patient_id": state["patient_id"]},
                actor=decision.get("actor", "Coordinator"),
                approval_id=approval_id,
                thread_id=state["thread_id"],
                run_id=state["run_id"],
                note=decision.get("note"),
            )
            repo.mark_approval_applied(db, approval_id)
            applied.append(proposal["id"])
    return {"applied": applied, "interrupted": False}


def compose_node(state: AgentState) -> dict:
    db = get_db()
    pending_for_run = [
        a
        for a in repo.list_approvals(db, state["patient_id"], status="pending")
        if a.get("run_id") == state["run_id"]
    ]
    reply, spoken = _compose_reply(state, pending_for_run)
    if reply:
        repo.add_memory(
            db,
            patient_id=state["patient_id"],
            kind="interaction",
            summary=f"Continuum response: {truncate(reply, 180)}",
            content=reply,
            tags=["agent", "response"],
            importance=0.35,
        )
    try:
        from .memory import refresh_patient_summary

        refresh_patient_summary(db, state["patient_id"], demo_today())
    except Exception as exc:  # pragma: no cover - never fail a run over the summary
        log.info("Summary refresh failed: %s", exc)
    return {"reply": reply, "spoken": spoken, "status": "completed"}


# ── Replies ──────────────────────────────────────────────────────────────


def _safety_line(extraction: dict) -> str | None:
    safety = (extraction or {}).get("safety") or {}
    if safety.get("emergency"):
        return (
            "This sounds potentially urgent. Please call 911 or go to the nearest emergency "
            "department now. I'm also notifying the care team."
        )
    if safety.get("urgent_symptoms"):
        return (
            "I can't assess symptoms, but this may need urgent attention. Please contact your "
            "care team now, or call 911 if you feel unsafe. I'm escalating this for clinician review."
        )
    if safety.get("medication_question"):
        return (
            "I can't advise on medication or dosage changes. I've escalated this for an authorized "
            "clinician, who will follow up. Please don't change your medication before speaking with them."
        )
    if safety.get("clinical_question"):
        return (
            "I can't give clinical advice. I've escalated this for a clinician to review and follow up."
        )
    return None


def _draft_reply(state: AgentState, pending: list[dict]) -> str:
    extraction = state.get("extraction") or {}
    created = sum(
        1 for p in state.get("proposals", []) if p["kind"] == "create_obligation"
    )
    reports = len(extraction.get("patient_reports") or [])
    notes = state.get("notes") or []
    safety = _safety_line(extraction)
    parts: list[str] = []
    if state["input_type"] == "document":
        parts.append(
            f"I processed “{state.get('title') or 'the care document'}” and identified {created} new follow-up "
            f"obligation{'s' if created != 1 else ''}."
        )
    elif state["input_type"].startswith("voice"):
        if reports:
            parts.append(
                f"Thanks — I've noted {reports} update{'s' if reports != 1 else ''} from the call as patient-reported."
            )
        else:
            parts.append("Thanks — I've recorded the call and checked it against the care plan.")
    else:
        parts.append("I checked the current care plan and recent history.")
    if notes:
        parts.append(" ".join(notes[:2]))
    if pending:
        parts.append(
            f"{len(pending)} change{'s' if len(pending) != 1 else ''} "
            f"need{'s' if len(pending) == 1 else ''} coordinator approval."
        )
    if safety:
        parts.append(safety)
    return " ".join(parts).strip()


def _compose_reply(state: AgentState, pending: list[dict] | None = None) -> tuple[str, str]:
    extraction = state.get("extraction") or {}
    safety = _safety_line(extraction)
    if pending is None:
        pending = state.get("pending_approvals") or []
    notes = state.get("notes") or []
    applied = state.get("applied") or []
    bundle = state.get("retrieval") or {}
    model = get_chat_model()

    if model is not None:
        try:
            system = (
                "You are Continuum, a post-discharge care-coordination agent. You help a care "
                "coordinator (or a patient on a voice call) understand what still needs to happen. "
                "You never diagnose, prescribe, adjust medication, or give clinical advice. "
                "Patient-reported updates are not verified completion. Be concise and concrete. "
                "Mention approval needs when relevant. Do not invent facts."
            )
            human = f"""Input type: {state['input_type']}
New input:
\"\"\"{state['input_text'][:4000]}\"\"\"

Retrieved context:
{render_context(bundle)}

Applied administrative changes: {len(applied)}
Notes: {notes}
Pending coordinator approvals: {[p.get('title') for p in pending]}
Safety line that must be included if present: {safety}

Write the response in at most 110 words. For a voice interaction, write natural spoken
language in at most 60 words. Never give clinical advice."""
            response = model.invoke([("system", system), ("human", human)])
            text = message_text(response)
            # Reasoning models can return null content when the budget is spent
            # on reasoning; treat that as a failure so the template fallback runs.
            if not text.strip() or text == "None":
                raise ValueError("model returned empty content")
            # Tidy spacing but keep line breaks: the console renders the reply
            # as Markdown, so lists and short paragraphs survive.
            text = "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()
            text = re.sub(r"\n{3,}", "\n\n", text)
            if safety and safety not in text:
                text = f"{text} {safety}".strip()
            spoken = _spoken_version(text, state, safety)
            return text, spoken
        except Exception as exc:  # pragma: no cover - network dependent
            log.info("LLM reply failed (%s); using template.", exc)

    reply = _draft_reply(state, pending)
    if applied:
        applied_ids = set(applied)
        titles = [
            str(proposal.get("title"))
            for proposal in state.get("proposals") or []
            if proposal.get("id") in applied_ids and proposal.get("title")
        ]
        if titles:
            reply += f" Approved and applied: {', '.join(titles[:3])}."
        else:
            reply += f" I recorded {len(applied)} administrative update{'s' if len(applied) != 1 else ''}."
    return reply, _spoken_version(reply, state, safety)


def _spoken_version(reply: str, state: AgentState, safety: str | None) -> str:
    if not state["input_type"].startswith("voice"):
        return ""
    spoken = reply
    if safety:
        spoken = safety
    # Voice replies should be short and easy to follow.
    if len(spoken) > 320:
        spoken = spoken[:317].rsplit(" ", 1)[0] + "…"
    return spoken


# ── Graph construction ───────────────────────────────────────────────────


@lru_cache
def get_graph():
    builder = StateGraph(AgentState)
    builder.add_node("ingest", ingest)
    builder.add_node("extract", extract_node)
    builder.add_node("retrieve", retrieve_node)
    builder.add_node("validate", validate_node)
    builder.add_node("propose", propose_node)
    builder.add_node("apply_auto", apply_auto_node)
    builder.add_node("await_approval", await_approval_node)
    builder.add_node("compose", compose_node)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "extract")
    builder.add_edge("extract", "retrieve")
    builder.add_edge("retrieve", "validate")
    builder.add_edge("validate", "propose")
    builder.add_edge("propose", "apply_auto")
    builder.add_edge("apply_auto", "await_approval")
    builder.add_edge("await_approval", "compose")
    builder.add_edge("compose", END)
    return builder.compile(checkpointer=get_checkpointer())


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 30}


def thread_snapshot(patient_id: str) -> dict:
    graph = get_graph()
    snapshot = graph.get_state(_config(patient_id))
    values = dict(snapshot.values or {})
    pending = repo.list_approvals(get_db(), patient_id, status="pending")
    return {
        "thread_id": patient_id,
        "paused": bool(snapshot.next),
        "next": list(snapshot.next or []),
        "values": values,
        "pending_approvals": pending,
        "checkpoint_id": (snapshot.config or {}).get("configurable", {}).get("checkpoint_id"),
    }


def run_agent(
    *,
    patient_id: str,
    input_type: str,
    text: str,
    actor: str = "Care coordinator",
    title: str | None = None,
    source_ref: str | None = None,
    transcript: list[dict] | None = None,
    metadata: dict | None = None,
) -> dict:
    graph = get_graph()
    thread_id = patient_id
    config = _config(thread_id)
    snapshot = graph.get_state(config)
    if snapshot.next and input_type != "chat":
        # A coordinator chat is paused for approval, but calls and documents
        # keep arriving. Never drop them: process on a sibling thread. Their
        # sensitive proposals are queued approvals, so they never interrupt.
        thread_id = f"{patient_id}:async"
        config = _config(thread_id)
        snapshot = graph.get_state(config)
    if snapshot.next:
        return {
            "status": "awaiting_approval",
            "patient_id": patient_id,
            "reply": (
                "This patient thread is paused at a human-in-the-loop approval. "
                "Resolve the pending approval before starting another interaction."
            ),
            "pending_approvals": repo.list_approvals(get_db(), patient_id, status="pending"),
        }

    run_id = new_id("run")
    initial: AgentState = {
        "patient_id": patient_id,
        "thread_id": thread_id,
        "run_id": run_id,
        "input_type": input_type,
        "input_text": text,
        "title": title or EVENT_TITLES.get(input_type, "Care interaction"),
        "source_ref": source_ref or "",
        "actor": actor,
        "transcript": transcript or [],
        "metadata": metadata or {},
    }
    result = graph.invoke(initial, config)
    after = graph.get_state(config)
    pending = repo.list_approvals(get_db(), patient_id, status="pending")
    interrupted = bool(after.next)
    reply = result.get("reply") or result.get("reply_draft") or ""
    return {
        "status": "awaiting_approval" if interrupted else "completed",
        "run_id": run_id,
        "patient_id": patient_id,
        "reply": reply,
        "spoken_response": result.get("spoken") or reply,
        "extraction": result.get("extraction"),
        "proposals": _public_proposals(result.get("proposals") or []),
        "notes": result.get("notes") or [],
        "applied": result.get("applied") or [],
        "interrupted": interrupted,
        "pending_approvals": [
            a for a in pending if a.get("run_id") == run_id or a.get("mode") == "interrupt"
        ],
        "retrieval": {
            "counts": (result.get("retrieval") or {}).get("counts", {}),
            "memories": [
                {
                    "id": m.get("id"),
                    "kind": m.get("kind"),
                    "summary": m.get("summary"),
                    "score": m.get("score"),
                    "retrieval": m.get("retrieval"),
                }
                for m in (result.get("retrieval") or {}).get("memories", [])
            ],
        },
    }


def resume_agent(patient_id: str, decisions: dict[str, dict]) -> dict:
    graph = get_graph()
    config = _config(patient_id)
    snapshot = graph.get_state(config)
    if not snapshot.next:
        return {"status": "not_paused", "patient_id": patient_id}
    result = graph.invoke(Command(resume=decisions), config)
    after = graph.get_state(config)
    values = dict(after.values or {})
    reply = values.get("reply") or result.get("reply") or values.get("reply_draft") or ""
    return {
        "status": "awaiting_approval" if after.next else "completed",
        "patient_id": patient_id,
        "reply": reply,
        "spoken_response": values.get("spoken") or reply,
        "applied": values.get("applied") or [],
    }


def _public_proposals(proposals: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    order: list[str] = []
    for p in proposals:
        dedupe_key = p.get("approval_id") or p.get("id") or ""
        entry = {
            "id": p.get("id"),
            "kind": p.get("kind"),
            "title": p.get("title"),
            "rationale": p.get("rationale"),
            "obligation_id": p.get("obligation_id"),
            "confidence": p.get("confidence"),
            "requires_approval": p.get("requires_approval"),
            "interrupt": p.get("interrupt"),
            "approval_id": p.get("approval_id"),
        }
        if dedupe_key not in merged:
            merged[dedupe_key] = entry
            order.append(dedupe_key)
        elif entry["interrupt"] and not merged[dedupe_key].get("interrupt"):
            merged[dedupe_key] = entry
    return [merged[key] for key in order]
