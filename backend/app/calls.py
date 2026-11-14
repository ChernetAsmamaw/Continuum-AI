"""Call Room: one voice call = one session the console can watch live.

A ``call_sessions`` document holds the call brief (what the agent will raise,
and why, from memory), every event as it happens (transcript turns, tool
calls, system steps) and, at the end, what the call changed. Events are
``$push``-ed, so the change stream carries them to the console instantly.

Three modes share everything except where the voice comes from:

* ``browser``   ElevenLabs in the browser; the coordinator plays the patient.
* ``phone``     ElevenLabs dials a real number (outbound).
* ``simulated`` a scripted call built from the patient's real open follow-ups
                that drives the *same* tool handlers. No keys needed; also the
                demo's safety net.

The call itself goes through the action ledger: intended → sent (connected)
→ confirmed (only once a transcript exists).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from pymongo.database import Database

from . import ledger
from . import repository as repo
from .config import settings
from .db import demo_today, get_db
from .util import new_id, parse_date, utcnow

log = logging.getLogger("continuum.calls")

MODES = ("browser", "phone", "simulated")
ACTOR = "Continuum voice agent"
SIM_DELAY = 1.6  # seconds between simulated turns (fast enough to demo, slow enough to follow)
PRIORITY = {"conflict": 0, "overdue": 1, "patient_reported": 2, "attempted": 3, "pending": 4, "scheduled": 5, "unknown": 6}


# ── brief ────────────────────────────────────────────────────────────────


def _why(o: dict, today) -> str:
    due = parse_date(o.get("due_date"))
    if o.get("conflicts"):
        c = o["conflicts"][-1]
        return f"Two dates on record: {c.get('recorded_value')} vs {c.get('reported_value')}. Confirm which is right."
    if o["status"] == "overdue" and due:
        return f"Due {due.isoformat()}, {max((today - due).days, 0)} day(s) overdue, no proof yet."
    if o["status"] == "patient_reported":
        return "Patient said it happened; no clinic proof yet. Ask where it was done so proof can be requested."
    if o["status"] == "scheduled" and o.get("confirmed_date"):
        return f"Clinic has it booked for {o['confirmed_date']}. Check the patient knows and can attend."
    if (o.get("category") or "").startswith("pending") or "pending result" in o["description"].lower():
        return "Result still pending at discharge. Nothing to ask the patient yet; mention only if they ask."
    if due:
        return f"Due {due.isoformat()}. Ask whether it's booked or done."
    return "Open follow-up. Ask whether it has happened."


def build_brief(db: Database, patient_id: str) -> dict:
    patient = repo.get_patient(db, patient_id) or {}
    today = demo_today()
    items = [o for o in repo.open_obligations(db, patient_id, today=today)]
    items.sort(key=lambda o: (0 if o.get("conflicts") else 1, PRIORITY.get(o["status"], 9), o.get("due_date") or "9999"))
    memories = repo.list_memories(db, patient_id, limit=40)
    recalled = [m["summary"] for m in memories if "response" not in (m.get("tags") or [])][:4]
    prefs = patient.get("preferences") or {}
    agenda = [
        {"key": "disclosure", "kind": "step", "title": "Say it's an AI assistant and ask consent to record",
         "why": "Required on every call." + ("" if prefs.get("consent_to_recording", True) else " Patient declined recording: don't record.")},
        {"key": "identity", "kind": "step", "title": "Verify identity before discussing care",
         "why": "Date of birth or phone on file must match."},
    ]
    for o in items[:6]:
        agenda.append({"key": o["id"], "kind": "followup", "title": o["description"], "status": o["status"],
                       "category": o.get("category"), "why": _why(o, today)})
    agenda.append({"key": "safety", "kind": "step", "title": "No clinical advice",
                   "why": "Medication or symptom questions go to a clinician; the agent says so and escalates."})
    return {
        "patient_id": patient_id,
        "patient_name": patient.get("name"),
        "language": prefs.get("language", "English"),
        "preferred_channel": prefs.get("channel"),
        "best_time": prefs.get("best_time"),
        "consent_to_automated_calls": prefs.get("consent_to_automated_calls", True),
        "consent_to_recording": prefs.get("consent_to_recording", True),
        "phone": patient.get("phone"),
        "agenda": agenda,
        "remembered": recalled,
    }


# ── what changed ─────────────────────────────────────────────────────────


def snapshot(db: Database, patient_id: str) -> dict:
    obligations = {
        o["id"]: {"status": o["status"], "description": o["description"], "conflicts": len(o.get("conflicts") or []),
                  "reported_date": o.get("reported_date")}
        for o in repo.list_obligations(db, patient_id, include_closed=True, today=demo_today())
    }
    approvals = {a["id"] for a in repo.list_approvals(db, patient_id, status="pending")}
    return {"obligations": obligations, "approvals": sorted(approvals)}


def diff(db: Database, before: dict, after: dict) -> list[dict]:
    effects: list[dict] = []
    for oid, now in after["obligations"].items():
        was = before["obligations"].get(oid)
        if was is None:
            effects.append({"type": "followup_created", "obligation_id": oid, "title": now["description"], "status": now["status"]})
        elif was["status"] != now["status"]:
            effects.append({"type": "status_changed", "obligation_id": oid, "title": now["description"],
                            "from": was["status"], "to": now["status"], "verified": now["status"] == "verified"})
        elif now["conflicts"] > was["conflicts"]:
            effects.append({"type": "conflict_recorded", "obligation_id": oid, "title": now["description"]})
        elif was.get("reported_date") != now.get("reported_date") and now.get("reported_date"):
            effects.append({"type": "date_reported", "obligation_id": oid, "title": now["description"], "date": now["reported_date"]})
    for aid in sorted(set(after["approvals"]) - set(before["approvals"])):
        a = repo.get_approval(db, aid) or {}
        effects.append({"type": "approval_queued", "approval_id": aid, "title": a.get("title"), "kind": a.get("kind"),
                        "audience": repo.approval_doc_audience(a) if a else None})
    return effects


# ── sessions ─────────────────────────────────────────────────────────────


def get_session(db: Database, session_id: str) -> dict | None:
    return db.call_sessions.find_one({"id": session_id}, {"_id": 0})


def active_session(db: Database, patient_id: str) -> dict | None:
    return db.call_sessions.find_one({"patient_id": patient_id, "status": {"$in": ["preparing", "live"]}}, {"_id": 0}, sort=[("created_at", -1)])


def _push(db: Database, session_id: str, event: dict) -> dict:
    event = {"at": utcnow(), "id": new_id("evt"), **event}
    db.call_sessions.update_one({"id": session_id}, {"$push": {"events": event}, "$set": {"updated_at": utcnow()}})
    return event


def available_modes() -> dict:
    voice = bool(settings.elevenlabs_agent_id)
    phone = bool(settings.elevenlabs_api_key and settings.elevenlabs_agent_id and settings.elevenlabs_phone_number_id)
    return {
        "browser": {"available": voice, "why": None if voice else "Set ELEVENLABS_AGENT_ID (and the API key) to talk to the agent in the browser."},
        "phone": {"available": phone, "why": None if phone else "Needs ELEVENLABS_API_KEY, ELEVENLABS_AGENT_ID and ELEVENLABS_PHONE_NUMBER_ID."},
        "simulated": {"available": True, "why": None},
        "agent_id": settings.elevenlabs_agent_id,
    }


def start(db: Database, *, patient_id: str, mode: str, actor: str, phone: str | None = None) -> dict:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if not available_modes()[mode]["available"]:
        raise ValueError(available_modes()[mode]["why"])
    patient = repo.get_patient(db, patient_id)
    if not patient:
        raise LookupError("Patient not found")
    current = active_session(db, patient_id)
    if current:
        return current
    session_id = new_id("call")
    action = ledger.intend(
        db, kind="voice_call", key=f"call:{session_id}", patient_id=patient_id,
        summary=f"{mode.title()} voice call with {patient['name']}",
        proof="A transcript of the conversation", actor=actor, confirm_within_hours=2,
        metadata={"session_id": session_id, "mode": mode},
    )
    session = {
        "id": session_id, "patient_id": patient_id, "mode": mode, "status": "preparing", "actor": actor,
        "created_at": utcnow(), "updated_at": utcnow(), "brief": build_brief(db, patient_id),
        "before": snapshot(db, patient_id), "events": [], "effects": [], "action_id": action["id"],
        "conversation_id": None, "phone": phone,
    }
    db.call_sessions.insert_one(dict(session))
    _push(db, session_id, {"kind": "system", "text": f"Call prepared ({mode})."})
    if mode == "simulated":
        threading.Thread(target=_simulate, args=(session_id,), name=f"sim-{session_id}", daemon=True).start()
    elif mode == "phone":
        _dial(db, session, patient, phone)
    return get_session(db, session_id) or session


def _dial(db: Database, session: dict, patient: dict, phone: str | None) -> None:
    from .scheduler import place_outbound_call

    target = dict(patient, phone=phone or patient.get("phone"))
    checkin = {"id": session["id"], "reason": "Coordinator-started check-in from the Call Room"}
    result = place_outbound_call(target, checkin, repo.open_obligations(db, patient["id"], today=demo_today()),
                                 call_session_id=session["id"])
    if result.get("ok"):
        connected(db, session["id"], conversation_id=result.get("conversation_id"), detail=f"Dialling {target.get('phone')}")
    else:
        ledger.mark_failed(db, session["action_id"], result.get("detail", "Call failed"))
        db.call_sessions.update_one({"id": session["id"]}, {"$set": {"status": "failed", "error": result.get("detail")}})
        _push(db, session["id"], {"kind": "system", "text": f"Call could not be placed: {result.get('detail')}"})


def connected(db: Database, session_id: str, *, conversation_id: str | None, detail: str = "Connected") -> dict | None:
    session = get_session(db, session_id)
    if not session:
        return None
    db.call_sessions.update_one({"id": session_id}, {"$set": {"status": "live", "conversation_id": conversation_id, "started_at": utcnow()}})
    ledger.mark_sent(db, session["action_id"], external_ref=conversation_id or f"call:{session_id}", detail=detail)
    if conversation_id:
        repo.upsert_voice_call(db, {"patient_id": session["patient_id"], "elevenlabs_conversation_id": conversation_id,
                                    "direction": "outbound" if session["mode"] == "phone" else "browser", "call_session_id": session_id})
    _push(db, session_id, {"kind": "system", "text": detail})
    return get_session(db, session_id)


def transcript(db: Database, session_id: str, *, role: str, text: str) -> dict | None:
    text = (text or "").strip()
    if not text or role not in {"agent", "patient"}:
        return None
    return _push(db, session_id, {"kind": "transcript", "role": role, "text": text})


def tool_event(db: Database, *, patient_id: str, tool: str, summary: str, before: dict, call_session_id: str | None = None,
               detail: dict | None = None) -> dict | None:
    """Record a voice tool call (and what it changed) on the patient's live call, if any."""
    # ElevenLabs fills call_session_id with a placeholder ("none") when the call
    # was not started from the Call Room; fall back to the patient's live call.
    session = get_session(db, call_session_id) if call_session_id and call_session_id != "none" else None
    session = session or active_session(db, patient_id)
    if not session or session["patient_id"] != patient_id:
        return None
    effects = diff(db, before, snapshot(db, patient_id))
    return _push(db, session["id"], {"kind": "tool", "tool": tool, "text": summary, "effects": effects, "detail": detail or {}})


def end(db: Database, session_id: str, *, reason: str = "Call ended") -> dict | None:
    session = get_session(db, session_id)
    if not session or session["status"] in {"ended", "failed"}:
        return session
    effects = diff(db, session["before"], snapshot(db, session["patient_id"]))
    turns = [e for e in session.get("events") or [] if e["kind"] == "transcript"]
    db.call_sessions.update_one({"id": session_id}, {"$set": {"status": "ended", "ended_at": utcnow(), "effects": effects, "end_reason": reason}})
    _push(db, session_id, {"kind": "system", "text": reason})
    if turns:
        ledger.confirm(db, external_ref=session.get("conversation_id") or f"call:{session_id}",
                       evidence=f"Transcript recorded ({len(turns)} turns)")
    elif session["mode"] != "phone":  # a phone call's transcript arrives later by webhook
        ledger.mark_failed(db, session["action_id"], "Ended with no conversation")
    repo.add_event(db, {"patient_id": session["patient_id"], "type": "voice_call", "title": f"Voice call ({session['mode']}) ended",
                        "detail": f"{len(turns)} turns, {len(effects)} change(s).", "actor": ACTOR, "metadata": {"call_session_id": session_id}})
    return get_session(db, session_id)


def on_post_call(db: Database, *, conversation_id: str | None, transcript_turns: list[dict]) -> None:
    """ElevenLabs post-call webhook: attach the full transcript and close the session."""
    if not conversation_id:
        return
    session = db.call_sessions.find_one({"conversation_id": conversation_id}, {"_id": 0})
    if not session:
        return
    if not [e for e in session.get("events") or [] if e["kind"] == "transcript"]:
        for turn in transcript_turns:
            role = "agent" if (turn.get("role") or "") in {"agent", "assistant"} else "patient"
            transcript(db, session["id"], role=role, text=turn.get("message") or turn.get("text") or "")
    end(db, session["id"], reason="Call completed (post-call transcript received)")


# ── simulated call ───────────────────────────────────────────────────────


def _script(db: Database, session: dict) -> list[dict]:
    """Agent/patient turns plus the tool each patient turn should trigger, from real open follow-ups."""
    brief = session["brief"]
    patient = repo.get_patient(db, session["patient_id"]) or {}
    first = (patient.get("name") or "there").split()[0]
    today = demo_today()
    steps: list[dict] = [
        {"role": "agent", "text": f"Hi {first}, this is Continuum, an AI assistant calling for your care team. This call is recorded for your care record. Is now an OK time?"},
        {"role": "patient", "text": "Yes, that's fine."},
        {"role": "agent", "text": "Thanks. Before we talk about your care, can you confirm your date of birth?"},
        {"role": "patient", "text": f"It's {patient.get('birth_date') or 'March 2nd, 1946'}.",
         "step": "identity", "note": "Identity verified against the record."},
    ]
    used: set[str] = set()
    spoken = lambda d: f"{d:%A}, {d:%B} {d.day}"  # noqa: E731 - "Friday, December 18"
    for item in [a for a in brief["agenda"] if a["kind"] == "followup"]:
        cat, title, oid = item.get("category") or "", item["title"], item["key"]
        if cat in used or len(used) >= 3:
            continue
        o = repo.get_obligation(db, oid) or {}
        if cat == "bloodwork":
            done = today - timedelta(days=2)
            steps += [{"role": "agent", "text": f"Your team asked for {title.lower()}. Have you had that done?"},
                      {"role": "patient", "text": f"Yes, I went to the lab on {done:%A} morning.",
                       "tool": "report", "args": {"obligation_id": oid, "report": f"Patient says the blood test was done at the lab on {spoken(done)}.",
                                                  "reported_status": "completed", "reported_date": done.isoformat()}},
                      {"role": "agent", "text": "Thank you, I've noted that. Your team will mark it done once the lab sends the result."}]
        elif cat in {"cardiology", "referral"}:
            booked = parse_date(o.get("confirmed_date"))
            if booked:
                said = booked + timedelta(days=6)
                steps += [{"role": "agent", "text": f"Your {title.lower()} is booked for {spoken(booked)}. Is that what you have too?"},
                          {"role": "patient", "text": f"No, I wrote down {spoken(said)}.",
                           "tool": "report", "args": {"obligation_id": oid, "report": f"Patient has the appointment as {spoken(said)}; clinic record says {spoken(booked)}.",
                                                      "reported_status": "scheduled", "reported_date": said.isoformat()}},
                          {"role": "agent", "text": "Thanks. That's different from what the clinic sent us, so I've asked the team to check and confirm with you."}]
            else:
                said = today + timedelta(days=10)
                steps += [{"role": "agent", "text": f"Have you been given a date for your {title.lower()}?"},
                          {"role": "patient", "text": f"Yes, they said {spoken(said)}.",
                           "tool": "report", "args": {"obligation_id": oid, "report": f"Patient says the appointment is on {spoken(said)}.",
                                                      "reported_status": "scheduled", "reported_date": said.isoformat()}},
                          {"role": "agent", "text": "Great, I've noted it. Your team will confirm the date with the clinic."}]
        elif cat == "home_care":
            steps += [{"role": "agent", "text": "A home-care nurse visit was planned. Has anyone been in touch?"},
                      {"role": "patient", "text": "No, nobody has called. Can you book it for me?",
                       "tool": "appointment", "args": {"obligation_id": oid, "requested_time": "this week, mornings", "notes": "Patient asks for the home-care visit to be booked."}},
                      {"role": "agent", "text": "I've asked the care team to book it. They'll confirm the time with you; I can't confirm it myself."}]
        elif cat == "family_physician":
            steps += [{"role": "agent", "text": f"You have a {title.lower()} coming up. Will you be able to make it?"},
                      {"role": "patient", "text": "Yes, my daughter is driving me."}]
        else:
            continue
        used.add(cat)
    # The safety moment happens on every simulated call: a medication question
    # is never answered, it's escalated to a clinician.
    steps += [{"role": "agent", "text": "Is there anything else on your mind?"},
              {"role": "patient", "text": "My ankles are a bit swollen. Should I take an extra water pill?",
               "tool": "escalate", "args": {"reason": "Patient asks whether to take an extra diuretic dose for ankle swelling.", "urgency": "routine"}},
              {"role": "agent", "text": "I can't give medical advice, so I've asked a clinician to call you back today. If you get short of breath or have chest pain, call 911."}]
    steps += [{"role": "agent", "text": "Would you like me to check in again later this week?"},
              {"role": "patient", "text": "Friday afternoon is good.",
               "tool": "callback", "args": {"when": "Friday afternoon", "reason": "Patient asked for a follow-up call."}},
              {"role": "agent", "text": "I'll call you Friday afternoon. Take care."}]
    return steps


def _run_tool(patient_id: str, tool: str, args: dict, conversation_id: str) -> str:
    from .workflow import voice_escalate, voice_report, voice_request_appointment, voice_request_callback

    if tool == "report":
        voice_report(patient_id=patient_id, conversation_id=conversation_id, direction="outbound", **args)
        return "Recorded patient report (unverified until the clinic confirms)"
    if tool == "escalate":
        voice_escalate(patient_id=patient_id, conversation_id=conversation_id, **args)
        return "Escalated to a clinician (no advice given)"
    if tool == "appointment":
        voice_request_appointment(patient_id=patient_id, conversation_id=conversation_id, **args)
        return "Booking requested; waiting on the care team to confirm"
    if tool == "callback":
        voice_request_callback(patient_id=patient_id, conversation_id=conversation_id, **args)
        return "Follow-up call scheduled"
    raise ValueError(tool)


def _simulate(session_id: str, delay: float | None = None) -> None:
    db = get_db()
    delay = SIM_DELAY if delay is None else delay
    try:
        session = get_session(db, session_id)
        conversation_id = f"sim-{session_id}"
        connected(db, session_id, conversation_id=conversation_id, detail="Simulated call connected")
        for step in _script(db, session):
            if (get_session(db, session_id) or {}).get("status") != "live":
                return  # ended early from the console
            time.sleep(delay)
            transcript(db, session_id, role=step["role"], text=step["text"])
            if step.get("step") == "identity":
                _push(db, session_id, {"kind": "tool", "tool": "identify", "text": step["note"], "effects": []})
            if step.get("tool"):
                before = snapshot(db, session["patient_id"])
                summary = _run_tool(session["patient_id"], step["tool"], step["args"], conversation_id)
                tool_event(db, patient_id=session["patient_id"], tool=step["tool"], summary=summary, before=before,
                           call_session_id=session_id, detail=step["args"])
        time.sleep(delay)
        end(db, session_id, reason="Simulated call completed")
    except Exception as exc:  # pragma: no cover - surfaced in the session instead of crashing the thread
        log.exception("Simulated call %s failed", session_id)
        db.call_sessions.update_one({"id": session_id}, {"$set": {"status": "failed", "error": str(exc)}})
        _push(db, session_id, {"kind": "system", "text": f"Simulation error: {exc}"})


def list_sessions(db: Database, patient_id: str, limit: int = 10) -> list[dict]:
    return list(db.call_sessions.find({"patient_id": patient_id}, {"_id": 0, "before": 0}).sort("created_at", -1).limit(limit))
