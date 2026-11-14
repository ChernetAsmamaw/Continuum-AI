"""Proactive follow-through: the part of Continuum that acts without being asked.

A sweep runs on a timer (``SCHEDULER_INTERVAL_SECONDS``) and on demand
(``POST /api/admin/sweep``). Each sweep:

1. **Dispatches due check-ins.** Scheduled check-ins whose time has come are
   placed as ElevenLabs outbound calls when telephony is configured, or turned
   into a coordinator task ("call the patient") when it is not. Patients are
   only ever dialled when their preferred channel is "Voice call", they have
   consented to automated calls, and it is inside calling hours
   (``CALL_WINDOW_*`` in ``CALL_TIMEZONE``); any other channel becomes a
   coordinator task to reach them that way.
2. **Chases overdue obligations.** Once an obligation is overdue, Continuum
   schedules a check-in with the patient. If it stays overdue past
   ``OVERDUE_ESCALATE_AFTER_DAYS``, it queues a coordinator task to contact the
   responsible owner directly.
3. **Nudges stale patient reports.** A patient-reported item that has waited
   too long for clinic verification gets a coordinator task to request proof.

Every action is idempotent (marks are stored on the obligation / check-in), so
running the sweep twice in a day never duplicates calls or tasks. Every action
writes an event and an audit record, like any other change in the system.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pymongo.database import Database

from . import ledger
from . import repository as repo
from .config import settings
from .db import demo_today, get_db
from .schemas import ApprovalKind, ApprovalStatus, ObligationStatus
from .util import new_id, parse_date, resolve_when, truncate, utcnow

log = logging.getLogger("continuum.scheduler")

ACTOR = "Continuum scheduler"
ELEVENLABS_OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"

VOICE_CHANNEL = "Voice call"
# A sweep claims a due check-in by moving it to "dispatching" before acting.
# A claim older than this belongs to a sweep that died mid-dispatch; the next
# sweep may take it over (the ledger then stops it from redialling).
CLAIM_STALE_AFTER = timedelta(minutes=10)
# Statuses that mean "a check-in for this item is still in flight".
PENDING_CHECKIN_STATUSES = ["scheduled", "dispatching", "dispatched", "awaiting_manual_call", "awaiting_outreach"]


# ── Time helpers ─────────────────────────────────────────────────────────


def _is_due(checkin: dict, today: date, now: datetime) -> bool | None:
    dt, d = resolve_when(checkin.get("scheduled_for"), today)
    if dt is not None:
        return dt <= now
    if d is not None:
        return d <= today
    return None  # unparseable


def _call_zone() -> timezone | ZoneInfo:
    try:
        return ZoneInfo(settings.call_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Unknown CALL_TIMEZONE %r; using UTC for calling hours.", settings.call_timezone)
        return timezone.utc


def within_calling_hours(now: datetime) -> bool:
    """True when ``now`` falls inside the automated-calling window."""
    local = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(_call_zone())
    start, end = settings.call_window_start_hour, settings.call_window_end_hour
    if start == end:
        return True  # window disabled
    if start < end:
        return start <= local.hour < end
    return local.hour >= start or local.hour < end  # window wraps midnight


def preferred_channel(prefs: dict) -> str:
    """The patient's preferred contact channel ("Voice call" when unset)."""
    channel = prefs.get("channel")
    return channel.strip() if isinstance(channel, str) and channel.strip() else VOICE_CHANNEL


# ── Outbound calling ─────────────────────────────────────────────────────


def telephony_configured() -> bool:
    return bool(
        settings.elevenlabs_api_key
        and settings.elevenlabs_agent_id
        and settings.elevenlabs_phone_number_id
    )


def _dialable(phone: str | None) -> str | None:
    """Return an E.164 number (``+<country><number>``), or None when unsafe to dial.

    None for missing or synthetic numbers and for anything ambiguous: dialling
    a guess could ring a stranger, so those become a coordinator call instead.

    - ``+44 20 7946 0958`` / ``0044 20 …``: international, kept as written.
    - ``416 555 0142`` / ``(416) 555-0142``: national, completed with
      ``DEFAULT_COUNTRY_CODE`` when that is ``1`` (10-digit NANP numbers);
      ``1 416 555 0142`` likewise.
    """
    if not phone or "synthetic" in phone.lower():
        return None
    text = phone.strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if text.startswith("+"):
        international = digits
    elif text.startswith("00"):
        international = digits[2:]
    else:
        cc = settings.default_country_code.strip().lstrip("+")
        if cc != "1":
            return None  # can't know the national number format: require +<cc>
        if len(digits) == 10 and digits[0] not in "01":
            international = "1" + digits
        elif len(digits) == 11 and digits[0] == "1" and digits[1] not in "01":
            international = digits
        else:
            return None
    # E.164: at most 15 digits; 8 is the shortest real-world full number.
    if not (8 <= len(international) <= 15) or international[0] == "0":
        return None
    return f"+{international}"


def next_calling_window(now: datetime) -> datetime:
    """When the next automated-calling window opens (``now`` if it is open)."""
    if within_calling_hours(now):
        return now
    zone = _call_zone()
    local = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(zone)
    opens = local.replace(hour=settings.call_window_start_hour, minute=0, second=0, microsecond=0)
    if opens <= local:
        opens += timedelta(days=1)
    return opens.astimezone(timezone.utc)


def _window_label() -> str:
    return f"{settings.call_window_start_hour:02d}:00–{settings.call_window_end_hour:02d}:00 {settings.call_timezone}"


def place_outbound_call(patient: dict, checkin: dict, open_items: list[dict]) -> dict:
    """Ask ElevenLabs to dial the patient. Returns {ok, conversation_id?, detail}."""
    number = _dialable(patient.get("phone"))
    if not number:
        return {"ok": False, "detail": "No dialable phone number on file"}
    payload = {
        "agent_id": settings.elevenlabs_agent_id,
        "agent_phone_number_id": settings.elevenlabs_phone_number_id,
        "to_number": number,
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "patient_id": patient["id"],
                "patient_name": patient.get("name", ""),
                "checkin_id": checkin["id"],
                "checkin_reason": checkin.get("reason", ""),
                "open_followups": "; ".join(o["description"] for o in open_items[:6]),
            }
        },
    }
    try:
        response = httpx.post(
            ELEVENLABS_OUTBOUND_URL,
            json=payload,
            headers={"xi-api-key": settings.elevenlabs_api_key or ""},
            timeout=20,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        # Never reached ElevenLabs: the call definitely did not go out.
        return {"ok": False, "detail": f"Outbound call failed: could not reach ElevenLabs ({type(exc).__name__})"}
    except Exception as exc:
        # The request may have been received (read timeout, dropped connection):
        # the phone may be ringing. Report "uncertain" so nobody redials blindly.
        return {"ok": False, "uncertain": True, "detail": f"Outbound call outcome unknown ({type(exc).__name__})"}
    if response.status_code >= 500:
        return {"ok": False, "uncertain": True, "detail": f"Outbound call outcome unknown (ElevenLabs HTTP {response.status_code})"}
    if response.status_code >= 400:
        return {"ok": False, "detail": f"Outbound call rejected (ElevenLabs HTTP {response.status_code})"}
    try:
        body = response.json()
    except ValueError:
        return {"ok": False, "uncertain": True, "detail": "Outbound call outcome unknown (unreadable ElevenLabs response)"}
    ok = body.get("success", True) is not False
    return {
        "ok": ok,
        "conversation_id": body.get("conversation_id"),
        "detail": truncate(str(body.get("message") or ("Call placed" if ok else "ElevenLabs declined the call")), 200),
    }


# ── Task helpers ─────────────────────────────────────────────────────────


def _queue_task(
    db: Database,
    *,
    patient: dict,
    title: str,
    rationale: str,
    changes: dict,
    obligation_id: str | None = None,
    evidence: str | None = None,
) -> dict:
    """Queue a coordinator task as a ``follow_up`` approval (approve = done)."""
    run_id = new_id("run")
    approval = _create_task_approval(db, patient=patient, title=title, rationale=rationale, changes=changes,
                                     obligation_id=obligation_id, evidence=evidence, run_id=run_id)
    action = ledger.intend(
        db,
        kind="coordinator_task",
        key=f"task:{approval['id']}",
        patient_id=patient["id"],
        summary=title,
        proof="Coordinator marks the task done",
        actor=ACTOR,
        confirm_within_hours=72,
        metadata={"approval_id": approval["id"]},
    )
    if action["status"] == ledger.INTENDED:
        ledger.mark_sent(db, action["id"], external_ref=approval["id"], detail="Queued in the coordinator approvals list")
    return approval


def _create_task_approval(db, *, patient, title, rationale, changes, obligation_id, evidence, run_id) -> dict:
    # follow_up approvals dedupe on (patient, kind, obligation_id); scheduler
    # tasks are tied to an obligation through ``changes`` instead, so pass the
    # task key as obligation_id to keep one open task per item.
    return repo.create_approval(
        db,
        {
            "patient_id": patient["id"],
            "run_id": run_id,
            "thread_id": patient["id"],
            "kind": ApprovalKind.follow_up.value,
            "mode": "queued",
            "obligation_id": obligation_id,
            "title": title,
            "rationale": rationale,
            "proposed_changes": changes,
            "evidence_summary": evidence,
            "confidence": 0.9,
            "source": "scheduler",
            "proposal": {
                "id": new_id("prop"),
                "kind": "follow_up",
                "patient_id": patient["id"],
                "title": title,
                "rationale": rationale,
                "evidence_summary": evidence or rationale,
                "confidence": 0.9,
                "changes": {**changes, "item": changes.get("item", title)},
                "requires_approval": True,
                "interrupt": False,
            },
        },
    )


def _mark(db: Database, obligation_id: str, key: str, value: str) -> None:
    db.obligations.update_one({"id": obligation_id}, {"$set": {f"sweep_marks.{key}": value}})


def _has_pending_checkin(db: Database, patient_id: str, obligation_id: str) -> bool:
    return (
        db.checkins.find_one(
            {
                "patient_id": patient_id,
                "obligation_id": obligation_id,
                "status": {"$in": PENDING_CHECKIN_STATUSES},
            }
        )
        is not None
    )


# ── Sweep steps ──────────────────────────────────────────────────────────


def _claim(db: Database, checkin: dict, now: datetime) -> bool:
    """Atomically take a due check-in so two sweeps can't both dispatch it.

    The background loop and ``POST /api/admin/sweep`` (or two API workers) can
    sweep at the same moment; only the one whose conditional update matches
    acts on the check-in.
    """
    claimed_at = now.isoformat()
    if checkin.get("status") == "scheduled":
        match = {"id": checkin["id"], "status": "scheduled"}
    else:  # a stale "dispatching" claim left by a crashed sweep
        match = {"id": checkin["id"], "status": "dispatching", "claimed_at": checkin.get("claimed_at")}
    result = db.checkins.update_one(match, {"$set": {"status": "dispatching", "claimed_at": claimed_at}})
    return result.modified_count == 1


def _stale_claim(checkin: dict, now: datetime) -> bool:
    try:
        claimed = datetime.fromisoformat(str(checkin.get("claimed_at")))
    except ValueError:
        return True
    if claimed.tzinfo is None:
        claimed = claimed.replace(tzinfo=timezone.utc)
    return now - claimed >= CLAIM_STALE_AFTER


def dispatch_due_checkins(db: Database, today: date, now: datetime) -> list[dict]:
    actions: list[dict] = []
    candidates = list(db.checkins.find({"status": {"$in": ["scheduled", "dispatching"]}}, {"_id": 0}))
    for checkin in candidates:
        if checkin["status"] == "dispatching" and not _stale_claim(checkin, now):
            continue  # another sweep is working on it right now
        due = _is_due(checkin, today, now)
        if due is None:
            db.checkins.update_one(
                {"id": checkin["id"]},
                {"$set": {"status": "needs_review", "updated_at": utcnow()}},
            )
            repo.add_event(
                db,
                {
                    "patient_id": checkin["patient_id"],
                    "type": "system",
                    "title": "Check-in time unclear",
                    "detail": f"Could not interpret “{checkin.get('scheduled_for')}”. A coordinator should set a date.",
                    "actor": ACTOR,
                    "metadata": {"checkin_id": checkin["id"]},
                },
            )
            actions.append({"type": "checkin_needs_review", "checkin_id": checkin["id"]})
            continue
        if not due:
            continue

        patient = repo.get_patient(db, checkin["patient_id"])
        if not patient:
            db.checkins.update_one(
                {"id": checkin["id"], "status": checkin["status"]},
                {"$set": {"status": "needs_review", "updated_at": utcnow(), "dispatch_detail": "Patient record not found."}},
            )
            actions.append({"type": "checkin_needs_review", "checkin_id": checkin["id"]})
            continue
        prefs = patient.get("preferences") or {}
        channel = preferred_channel(prefs)
        # Consent is opt-in: a record without the flag (e.g. created by a path
        # that forgot to ask) is treated as "no", never as "yes".
        consented = prefs.get("consent_to_automated_calls") is True
        will_dial = channel == VOICE_CHANNEL and consented and telephony_configured()
        # Texts and emails keep the same quiet hours as calls.
        will_message = channel != VOICE_CHANNEL and consented and _message_channel_ready(channel, patient)
        if (will_dial or will_message) and not within_calling_hours(now):
            # Stays "scheduled"; the first sweep inside calling hours places it.
            # Say so on the check-in and in the sweep result, so a waiting
            # call doesn't look like a scheduler that did nothing.
            opens = next_calling_window(now)
            reason = f"Waiting for calling hours ({_window_label()}); will call after {opens.isoformat(timespec='minutes')}."
            if checkin.get("dispatch_detail") != reason:
                db.checkins.update_one(
                    {"id": checkin["id"], "status": "scheduled"},
                    {"$set": {"dispatch_detail": reason, "held_until": opens.isoformat()}},
                )
            actions.append({
                "type": "checkin_held",
                "checkin_id": checkin["id"],
                "patient_id": patient["id"],
                "reason": "outside_calling_hours",
                "detail": reason,
                "calls_after": opens.isoformat(),
            })
            continue
        if not _claim(db, checkin, now):
            continue  # another sweep got there first
        open_items = repo.open_obligations(db, patient["id"], today=today)

        sent_message = None
        if will_message:
            from .messaging import send_checkin

            sent_message = send_checkin(db, patient, checkin)
        if sent_message and sent_message["ok"]:
            status, detail = "dispatched", f"Check-in sent by {channel}: {sent_message['detail']}"
        elif channel != VOICE_CHANNEL:
            # Respect the patient's preference: never robo-call someone who
            # asked for text, email or the portal. A coordinator reaches them.
            status = "awaiting_outreach"
            detail = f"Preferred channel is {channel}; no automated call placed." + (
                f" Message not sent: {sent_message['detail']}" if sent_message else ""
            )
            _queue_task(
                db,
                patient=patient,
                title=f"Contact {patient['name']} by {channel} for a scheduled check-in",
                rationale=(
                    f"Check-in due: {(checkin.get('reason') or 'scheduled follow-up').rstrip('.')}. "
                    f"{patient['name']} prefers {channel}, so Continuum did not call."
                    + ("" if consented else " The patient has also not consented to automated calls.")
                ),
                changes={
                    "item": f"check-in by {channel}",
                    "checkin_id": checkin["id"],
                    "channel": channel,
                    "open_followups": [o["description"] for o in open_items],
                },
                obligation_id=f"checkin:{checkin['id']}",
            )
        elif not consented:
            status, detail = "skipped_no_consent", "Patient has not consented to automated calls."
            _queue_task(
                db,
                patient=patient,
                title=f"Contact {patient['name']} by {prefs.get('channel', 'their preferred channel')}",
                rationale=f"A check-in was due ({checkin.get('reason', 'follow-up')}) but the patient has not consented to automated calls.",
                changes={"item": "manual check-in", "checkin_id": checkin["id"]},
                obligation_id=f"checkin:{checkin['id']}",
            )
        elif telephony_configured():
            # Ledger first: if we die mid-call, the intent survives and is
            # flagged "unknown" instead of silently dialling the patient twice.
            action = ledger.intend(
                db,
                kind="outbound_call",
                key=f"checkin:{checkin['id']}:call",
                patient_id=patient["id"],
                summary=f"Check-in call to {patient['name']}",
                proof="ElevenLabs post-call transcript for this conversation",
                actor=ACTOR,
                confirm_within_hours=6,
                metadata={"checkin_id": checkin["id"]},
            )
            if action.get("reused") and action["status"] == ledger.INTENDED:
                # A previous attempt decided to call and never recorded the outcome
                # (crash mid-request). The call may have gone out: don't dial again.
                action = ledger.mark_unknown(db, action["id"], "Previous attempt was interrupted mid-call; not redialling automatically.")
            if action["status"] != ledger.INTENDED:
                result = {"ok": action["status"] in {ledger.SENT, ledger.CONFIRMED}, "detail": f"Already {action['status']} (action {action['id']})", "conversation_id": action.get("external_ref")}
            else:
                result = place_outbound_call(patient, checkin, open_items)
                if result["ok"]:
                    ledger.mark_sent(db, action["id"], external_ref=result.get("conversation_id"), detail=result["detail"])
                elif result.get("uncertain"):
                    ledger.mark_unknown(db, action["id"], result["detail"])
                else:
                    ledger.mark_failed(db, action["id"], result["detail"])
            if result["ok"]:
                status, detail = "dispatched", result["detail"]
                if result.get("conversation_id"):
                    repo.upsert_voice_call(
                        db,
                        {
                            "patient_id": patient["id"],
                            "elevenlabs_conversation_id": result["conversation_id"],
                            "direction": "outbound",
                            "checkin_id": checkin["id"],
                        },
                    )
            else:
                status, detail = "awaiting_manual_call", result["detail"]
        else:
            status, detail = "awaiting_manual_call", "Voice calling is not configured."

        if status == "awaiting_manual_call":
            _queue_task(
                db,
                patient=patient,
                title=f"Call {patient['name']} for a scheduled check-in",
                rationale=(
                    f"Check-in due: {(checkin.get('reason') or 'scheduled follow-up call').rstrip('.')}. "
                    f"{'Voice calling is not set up, so this call needs a person.' if detail == 'Voice calling is not configured.' else detail}"
                ),
                changes={
                    "item": "check-in call",
                    "checkin_id": checkin["id"],
                    "open_followups": [o["description"] for o in open_items],
                },
                obligation_id=f"checkin:{checkin['id']}",
            )

        db.checkins.update_one(
            {"id": checkin["id"]},
            {
                "$set": {"status": status, "dispatched_at": utcnow(), "dispatch_detail": detail, "channel": channel},
                "$unset": {"claimed_at": "", "held_until": ""},
            },
        )
        repo.add_event(
            db,
            {
                "patient_id": patient["id"],
                "type": "voice_call" if status == "dispatched" else "system",
                "title": {
                    "dispatched": "Outbound check-in call placed",
                    "awaiting_manual_call": "Check-in due — coordinator call needed",
                    "skipped_no_consent": "Check-in due — no consent for automated calls",
                    "awaiting_outreach": f"Check-in due — coordinator to contact by {channel}",
                }[status],
                "detail": truncate(f"{checkin.get('reason', '')} {detail}".strip(), 400),
                "actor": ACTOR,
                "metadata": {"checkin_id": checkin["id"]},
            },
        )
        repo.audit(
            db,
            patient_id=patient["id"],
            actor=ACTOR,
            action="Dispatched check-in",
            detail=f"{status.replace('_', ' ').capitalize()}. {detail}",
            before={"status": "scheduled"},
            after={"checkin_id": checkin["id"], "status": status},
        )
        actions.append({"type": "checkin", "checkin_id": checkin["id"], "patient_id": patient["id"], "status": status})
    return actions


def chase_overdue(db: Database, today: date) -> list[dict]:
    actions: list[dict] = []
    for patient in repo.list_patients(db, today):
        full = repo.get_patient(db, patient["id"]) or patient
        consent = (full.get("preferences") or {}).get("consent_to_automated_calls") is True
        for o in repo.open_obligations(db, patient["id"], today=today):
            if o["status"] != ObligationStatus.overdue.value:
                continue
            marks = o.get("sweep_marks") or {}
            days = o.get("days_overdue", 0)

            if (
                days >= settings.overdue_checkin_after_days
                and "overdue_checkin" not in marks
            ):
                if consent and not _has_pending_checkin(db, patient["id"], o["id"]):
                    checkin = repo.schedule_checkin(
                        db,
                        {
                            "patient_id": patient["id"],
                            "obligation_id": o["id"],
                            "scheduled_for": today.isoformat(),
                            "reason": f"{o['description']} is {days} day(s) overdue — ask whether it happened or is booked.",
                            "created_by": ACTOR,
                        },
                    )
                    detail = f"Scheduled a check-in call ({checkin['id']})."
                else:
                    detail = "No automated call (no consent or one already pending)."
                _mark(db, o["id"], "overdue_checkin", today.isoformat())
                repo.add_event(
                    db,
                    {
                        "patient_id": patient["id"],
                        "type": "system",
                        "title": f"Overdue: {o['description']}",
                        "detail": f"{days} day(s) past due ({o.get('due_date')}). {detail}",
                        "actor": ACTOR,
                        "metadata": {"obligation_id": o["id"]},
                    },
                )
                repo.audit(
                    db,
                    patient_id=patient["id"],
                    actor=ACTOR,
                    action="Overdue follow-up chased",
                    detail=f"{o['description']}: {detail}",
                    after={"obligation_id": o["id"], "days_overdue": days},
                )
                actions.append({"type": "overdue_checkin", "obligation_id": o["id"], "patient_id": patient["id"]})

            if (
                days >= settings.overdue_escalate_after_days
                and "overdue_escalated" not in marks
            ):
                approval = _queue_task(
                    db,
                    patient=full,
                    title=f"Contact {o.get('owner') or 'the owner'}: {o['description']} is {days} days overdue",
                    rationale=(
                        f"{o['description']} for {full['name']} was due {o.get('due_date')} and is still open. "
                        "Confirm with the responsible clinic whether it happened, is booked, or needs re-referral."
                    ),
                    changes={"item": f"owner outreach for {o['description']}", "obligation_id": o["id"], "days_overdue": days},
                    obligation_id=f"overdue:{o['id']}",
                    evidence=o.get("latest_evidence") or "No booking or completion has been recorded.",
                )
                _mark(db, o["id"], "overdue_escalated", today.isoformat())
                actions.append({"type": "overdue_escalated", "obligation_id": o["id"], "approval_id": approval["id"]})
    return actions


def nudge_stale_reports(db: Database, today: date) -> list[dict]:
    actions: list[dict] = []
    threshold = settings.stale_report_after_days
    for doc in list(db.obligations.find({"status": ObligationStatus.patient_reported.value}, {"_id": 0})):
        if (doc.get("sweep_marks") or {}).get("stale_report"):
            continue
        reported_at = next(
            (h.get("at") for h in reversed(doc.get("history") or []) if h.get("status") == doc["status"]),
            doc.get("updated_at"),
        )
        since = parse_date(reported_at)
        if not since or (today - since).days < threshold:
            continue
        patient = repo.get_patient(db, doc["patient_id"])
        if not patient:
            continue
        waiting = (today - since).days
        approval = _queue_task(
            db,
            patient=patient,
            title=f"Request proof: {doc['description']} (patient-reported {waiting} days ago)",
            rationale=(
                f"{patient['name']} said this happened, but no clinic record has arrived in {waiting} days. "
                f"Ask {doc.get('owner') or 'the clinic'} for a visit note or confirmation so the loop can close."
            ),
            changes={"item": f"verification request for {doc['description']}", "obligation_id": doc["id"]},
            obligation_id=f"stale:{doc['id']}",
        )
        _mark(db, doc["id"], "stale_report", today.isoformat())
        actions.append({"type": "stale_report", "obligation_id": doc["id"], "approval_id": approval["id"]})
    return actions


# ── Entry points ─────────────────────────────────────────────────────────


def resume_ingest_jobs(db: Database) -> list[dict]:
    """Finish ingestion jobs a crash or restart left half-done."""
    if not settings.ehr_fhir_base_url:
        return []
    from .ingest.jobs import resume_pending

    return [{"type": "ingest_resumed", "job_id": r["job_id"], "patient_id": r.get("patient_id"), "outcome": r.get("status")} for r in resume_pending(db)]


def poll_ehr_results(db: Database) -> list[dict]:
    """Ask the EHR for results behind open follow-ups (no-op when not configured)."""
    if not (settings.ehr_fhir_base_url and settings.ehr_poll_enabled):
        return []
    from .ingest.fhir_client import FHIRError, client_from_settings
    from .ingest.poller import poll_ehr

    client = client_from_settings()
    try:
        result = poll_ehr(db, client)
    except FHIRError as exc:
        log.warning("EHR poll failed: %s", exc)
        return []
    finally:
        client.close()
    return [
        {
            "type": "ehr_result",
            "patient_id": r.get("patient_id"),
            "obligation_id": r.get("obligation_id"),
            "outcome": r.get("outcome"),
            "source_ref": r.get("source_ref"),
        }
        for r in result["results"]
    ]


def _message_channel_ready(channel: str, patient: dict) -> bool:
    """Can Continuum send this patient's preferred message channel itself?"""
    from .notify import email_configured, sms_configured

    if channel == "Text":
        return sms_configured() and bool(_dialable(patient.get("phone")))
    if channel == "Email":
        return email_configured() and "@" in (patient.get("email") or "")
    if channel == "Portal":
        return (email_configured() and "@" in (patient.get("email") or "")) or (sms_configured() and bool(_dialable(patient.get("phone"))))
    return False


def _repage(db: Database, now: datetime) -> list[dict]:
    from .notify import repage_unacknowledged

    return repage_unacknowledged(db, now)


def retry_tcm_contacts(db: Database, today: date) -> list[dict]:
    """Keep trying for the 2-business-day contact.

    After a failed attempt, book the next try for the next business day while
    the window is open and fewer than two attempts are documented, so the TCM
    requirement is met either way. A missed window becomes a coordinator task.
    """
    from . import tcm

    actions: list[dict] = []
    for patient in db.patients.find({}, {"_id": 0}):
        state = tcm.clock(db, patient, today)
        if not state.get("eligible"):
            continue
        marks = (patient.get("sweep_marks") or {})
        if state["contact_status"] == "missed" and marks.get("tcm_missed") != state["episode_id"]:
            approval = _create_task_approval(
                db,
                patient=patient,
                title=f"TCM contact window missed for {patient['name']}",
                rationale=(
                    f"Discharged {state['discharge_date']}; interactive contact was due by {state['contact_due']} "
                    f"and {state['contact_attempts']} attempt(s) are documented. Call today and log the outcome."
                ),
                changes={"item": "TCM contact", "contact_due": state["contact_due"]},
                obligation_id=f"tcm:{state['episode_id']}",
                evidence=f"{state['contact_attempts']} documented attempt(s)",
                run_id=None,
            )
            db.patients.update_one({"id": patient["id"]}, {"$set": {"sweep_marks.tcm_missed": state["episode_id"]}})
            actions.append({"type": "tcm_contact_missed", "patient_id": patient["id"], "approval_id": approval["id"]})
            continue
        if state["contact_status"] not in ("open", "due_today"):
            continue
        contacts = state["contacts"]
        if not contacts or contacts[-1].get("interactive"):
            continue
        if db.checkins.find_one({"patient_id": patient["id"], "kind": "tcm_contact", "status": {"$in": ["scheduled", "dispatching"]}}):
            continue
        target = today + timedelta(days=1)
        while target.weekday() >= 5:
            target += timedelta(days=1)
        if target > (parse_date(state["contact_due"]) or target):
            target = today
        checkin = repo.schedule_checkin(
            db,
            {
                "patient_id": patient["id"],
                "episode_id": state["episode_id"],
                "kind": "tcm_contact",
                "scheduled_for": target.isoformat(),
                "reason": f"Retry post-discharge contact (attempt {state['contact_attempts'] + 1}); due {state['contact_due']}.",
                "created_by": "Continuum (sweep)",
            },
        )
        actions.append({"type": "tcm_contact_retry", "patient_id": patient["id"], "checkin_id": checkin["id"]})
    return actions


def run_sweep(today: date | None = None, now: datetime | None = None) -> dict:
    db = get_db()
    today = today or demo_today()
    now = now or datetime.now(timezone.utc)
    # Judge existing actions first, so ones created by this sweep aren't
    # measured against a fast-forwarded demo clock.
    reconciled = [
        {"type": f"action_{a['status']}", "patient_id": a["patient_id"], "action_id": a["id"]}
        for a in ledger.reconcile(db, now)
    ]
    actions = reconciled + (
        dispatch_due_checkins(db, today, now)
        + chase_overdue(db, today)
        + nudge_stale_reports(db, today)
        + retry_tcm_contacts(db, today)
        + _repage(db, now)
        + poll_ehr_results(db)
        + resume_ingest_jobs(db)
    )
    pending_tasks = len(
        [
            a
            for a in repo.list_approvals(db, status=ApprovalStatus.pending.value)
            if a.get("source") == "scheduler"
        ]
    )
    taken = [a for a in actions if a["type"] != "checkin_held"]
    db.scheduler_runs.insert_one(
        {"id": new_id("swp"), "ran_at": utcnow(), "today": today.isoformat(), "actions": len(taken),
         "held": len(actions) - len(taken)}
    )
    if taken:
        log.info("Sweep took %d action(s).", len(taken))
    return {
        "ok": True,
        "today": today.isoformat(),
        "telephony": telephony_configured(),
        "calling_hours": {"window": _window_label(), "open_now": within_calling_hours(now),
                          "next_opens": next_calling_window(now).isoformat()},
        "actions": actions,
        "counts": {
            kind: sum(1 for a in actions if a["type"] == kind)
            for kind in {a["type"] for a in actions}
        },
        "pending_scheduler_tasks": pending_tasks,
    }


def last_run() -> dict | None:
    return get_db().scheduler_runs.find_one({}, {"_id": 0}, sort=[("ran_at", -1)])


async def scheduler_loop(interval: int) -> None:
    """Background loop started from the FastAPI lifespan."""
    log.info("Scheduler running every %ss.", interval)
    while True:
        try:
            await asyncio.to_thread(run_sweep)
        except Exception as exc:  # pragma: no cover - defensive
            log.exception("Scheduler sweep failed: %s", exc)
        await asyncio.sleep(interval)
