"""Patient check-ins by text, email and a secure check-in page (portal).

A patient who chose Text, Email or Portal gets a short message with a link
to their check-in page instead of a phone call. The message itself carries
no clinical detail. The page asks for date of birth (link + DOB = two
identifiers) before it shows anything, then lets the patient mark each
follow-up done / booked / not yet, write a message, or ask for a call back.

Everything a patient sends is a *patient report*: it runs through the same
agent graph and rules as a voice call (evidence, never verification;
clinical questions escalated). Replying "STOP" to a text opts out at once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import date

from pymongo.database import Database

from . import ledger, notify, tcm
from . import repository as repo
from .auth import create_token, decode_token
from .config import settings
from .util import parse_date, utcnow

CHECKIN_SCOPE = "patient_checkin"
SESSION_SCOPE = "patient_session"
LINK_TTL_MINUTES = 72 * 60
SESSION_TTL_MINUTES = 30
MAX_DOB_FAILURES = 5
OPT_OUT = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}

STATUS_WORDS = {"done": "completed", "booked": "scheduled", "not_yet": "not yet booked", "need_help": "needs help"}


def first_name(patient: dict) -> str:
    return (patient.get("name") or "there").split(" ")[0]


def checkin_link(patient: dict, checkin_id: str | None = None) -> str:
    token = create_token(
        sub=patient["id"], email="", name=first_name(patient), role="patient",
        ttl_minutes=LINK_TTL_MINUTES, scope=CHECKIN_SCOPE,
    )
    return f"{settings.public_app_url.rstrip('/')}/checkin/{token}"


def _message_text(patient: dict, link: str) -> str:
    return (
        f"Hi {first_name(patient)}, this is your care team checking in after your hospital stay. "
        f"Tell us how you're doing and confirm your follow-up appointments: {link} "
        "Reply STOP to opt out. If this is an emergency, call 911."
    )


def send_checkin(db: Database, patient: dict, checkin: dict) -> dict:
    """Send a check-in by the patient's channel. Returns {ok, detail, channel}."""
    prefs = patient.get("preferences") or {}
    channel = prefs.get("channel") or "Voice call"
    link = checkin_link(patient, checkin.get("id"))
    text = _message_text(patient, link)
    if channel == "Text":
        route = ("SMS", lambda: notify.send_sms(patient.get("phone") or "", text))
    elif channel == "Email" or (channel == "Portal" and patient.get("email")):
        route = ("Email", lambda: notify.send_email(patient.get("email") or "", "Checking in after your hospital stay", text))
    elif channel == "Portal":
        route = ("SMS", lambda: notify.send_sms(patient.get("phone") or "", text))
    else:
        return {"ok": False, "detail": f"{channel} is not a message channel", "channel": channel}

    via, send = route
    action = ledger.intend(
        db,
        kind="patient_message",
        key=f"checkin:{checkin['id']}:message",
        patient_id=patient["id"],
        summary=f"Check-in by {channel} ({via}) to {patient['name']}",
        proof="patient reply or check-in page submission",
        actor="Continuum (follow-through)",
        confirm_within_hours=72,
        metadata={"checkin_id": checkin["id"], "channel": channel},
    )
    if action["status"] != ledger.INTENDED:
        return {"ok": action["status"] in {ledger.SENT, ledger.CONFIRMED}, "detail": f"Already {action['status']}", "channel": channel}
    result = send()
    if result["ok"]:
        ledger.mark_sent(db, action["id"], external_ref=f"msg:{patient['id']}:{checkin['id']}", detail=result["detail"])
        tcm.record_contact(db, patient_id=patient["id"], outcome="message_sent", channel=channel,
                           actor="Continuum (follow-through)", ref=f"msg:{checkin['id']}",
                           note="Check-in link sent")
    else:
        ledger.mark_failed(db, action["id"], result["detail"])
    return {**result, "channel": channel}


# ── the check-in page ────────────────────────────────────────────────────


class PortalError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _patient_from(token: str, scope: str) -> dict:
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise PortalError(401, "This link has expired. Ask your care team for a new one.") from exc
    if payload.get("scope") != scope:
        raise PortalError(401, "This link is not valid.")
    patient = repo.get_patient(repo_db(), payload["sub"])
    if not patient:
        raise PortalError(404, "This link is not valid.")
    return patient


def repo_db() -> Database:
    from .db import get_db

    return get_db()


def open_link(token: str) -> dict:
    patient = _patient_from(token, CHECKIN_SCOPE)
    return {"first_name": first_name(patient), "verify_with": "birth_date" if patient.get("birth_date") else "phone_last4"}


def verify(token: str, answer: str, today: date) -> dict:
    """Second identifier: date of birth (or the phone's last 4 when no DOB is on file)."""
    db = repo_db()
    patient = _patient_from(token, CHECKIN_SCOPE)
    failures = (patient.get("portal") or {}).get("failures", 0)
    if failures >= MAX_DOB_FAILURES:
        raise PortalError(423, "Too many attempts. Please call your care team.")
    if patient.get("birth_date"):
        stated = parse_date(answer)
        ok = bool(stated and stated.isoformat() == patient["birth_date"])
    else:
        digits = "".join(ch for ch in (patient.get("phone") or "") if ch.isdigit())
        ok = bool(digits) and "".join(ch for ch in answer if ch.isdigit()) == digits[-4:]
    if not ok:
        db.patients.update_one({"id": patient["id"]}, {"$set": {"portal.failures": failures + 1}})
        repo.audit(db, patient_id=patient["id"], actor="Patient check-in page", action="Check-in identity check failed",
                   detail=f"Attempt {failures + 1} of {MAX_DOB_FAILURES}")
        raise PortalError(403, "Those details don't match our records.")
    db.patients.update_one({"id": patient["id"]}, {"$set": {"portal.failures": 0, "portal.last_verified_at": utcnow()}})
    session = create_token(sub=patient["id"], email="", name=first_name(patient), role="patient",
                           ttl_minutes=SESSION_TTL_MINUTES, scope=SESSION_SCOPE)
    items = repo.open_obligations(db, patient["id"], today=today)
    return {
        "session": session,
        "first_name": first_name(patient),
        "follow_ups": [
            {"id": o["id"], "description": o["description"], "due_date": o.get("due_date"),
             "confirmed_date": o.get("confirmed_date"), "status": o.get("status")}
            for o in items
            if o.get("category") != tcm.MED_REC
        ],
    }


def submit(session: str, updates: list[dict], message: str | None, callback: bool, today: date) -> dict:
    """Record what the patient told us. Every line is a patient report."""
    from .agent.graph import run_agent

    db = repo_db()
    patient = _patient_from(session, SESSION_SCOPE)
    open_ids = {o["id"]: o for o in repo.open_obligations(db, patient["id"], today=today)}
    lines = []
    for update in updates:
        obligation = open_ids.get(update.get("obligation_id"))
        status = update.get("status")
        if not obligation or status not in STATUS_WORDS:
            continue
        when = parse_date(update.get("date"))
        line = f"Regarding {obligation['description']}: the patient says it is {STATUS_WORDS[status]}"
        if when:
            line += f" (reported date: {when.isoformat()})"
        lines.append(line + ".")
    if message and message.strip():
        lines.append(f"Patient message: {message.strip()[:2000]}")
    if callback:
        lines.append("The patient asked for a call back from the care team.")
    if not lines:
        raise PortalError(422, "Tell us about at least one follow-up, or write a message.")

    result = run_agent(
        patient_id=patient["id"],
        input_type="voice_report",
        text=" ".join(lines),
        actor="Patient (check-in page)",
        title="Patient check-in submitted",
        source_ref="Patient check-in page",
        metadata={"channel": "portal"},
    )
    if callback:
        repo.schedule_checkin(db, {"patient_id": patient["id"], "scheduled_for": today.isoformat(),
                                   "reason": "Patient asked for a call back from the check-in page", "created_by": "Patient"})
    tcm.record_contact(db, patient_id=patient["id"], outcome="reached", channel="Portal",
                       actor="Patient (check-in page)", direction="inbound", note="Check-in page submitted")
    for action in db.actions.find({"kind": "patient_message", "patient_id": patient["id"], "status": {"$in": ["sent", "unconfirmed"]}}, {"_id": 0}):
        if action.get("external_ref"):
            ledger.confirm(db, external_ref=action["external_ref"], kind="patient_message", evidence="Patient submitted the check-in page")
    return {"ok": True, "message": "Thank you. Your care team has your update and will confirm with the clinic.",
            "escalated": any(p.get("kind") == "escalate" for p in result.get("proposals") or [])}


# ── inbound SMS (Twilio webhook) ─────────────────────────────────────────


def twilio_signature_ok(url: str, params: dict[str, str], signature: str | None) -> bool:
    """Twilio's X-Twilio-Signature: base64(HMAC-SHA1(auth_token, url + sorted k+v))."""
    if not settings.twilio_auth_token:
        return settings.is_development  # unsigned only in development
    if not signature:
        return False
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(settings.twilio_auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def inbound_sms(sender: str, body: str, sid: str | None, today: date) -> dict:
    from .agent.graph import run_agent

    db = repo_db()
    digits = "".join(ch for ch in sender if ch.isdigit())[-10:]
    matches = [p for p in db.patients.find({}, {"_id": 0})
               if digits and "".join(ch for ch in (p.get("phone") or "") if ch.isdigit())[-10:] == digits]
    if len(matches) != 1:
        return {"ok": False, "reply": None, "detail": "Sender does not match exactly one patient."}
    patient = matches[0]
    text = (body or "").strip()
    if text.lower() in OPT_OUT:
        db.patients.update_one({"id": patient["id"]}, {"$set": {"preferences.consent_to_automated_calls": False,
                                                              "preferences.opted_out_at": utcnow()}})
        repo.audit(db, patient_id=patient["id"], actor="Patient (text)", action="Opted out of automated messages",
                   detail=f"Replied {text.upper()}", before={"consent_to_automated_calls": True},
                   after={"consent_to_automated_calls": False})
        return {"ok": True, "reply": "You're unsubscribed from automated messages. Your care team may still call you."}
    run_agent(
        patient_id=patient["id"],
        input_type="voice_report",
        text=f"Patient text message: {text[:1600]}",
        actor="Patient (text)",
        title="Patient text message",
        source_ref=sid,
        metadata={"channel": "sms"},
    )
    tcm.record_contact(db, patient_id=patient["id"], outcome="reached", channel="Text", actor="Patient (text)",
                       direction="inbound", ref=sid, note="Patient replied by text")
    return {"ok": True, "reply": "Thanks, your care team has your message. If this is an emergency, call 911."}
