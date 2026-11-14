"""Outbound SMS (Twilio) and email (SMTP), plus on-call clinician paging.

Every send goes through the action ledger first (intended → sent / failed),
so a page that could not go out is visible, never silently dropped.

Paging: an urgent or emergency escalation pages the on-call clinician by SMS
*and* email the moment it is queued. If nobody acknowledges it within
``PAGE_ACK_MINUTES_*``, the sweep pages again, up to ``PAGE_MAX_ATTEMPTS``.
"""

from __future__ import annotations

import logging
import smtplib
import threading
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx
from pymongo.database import Database

from . import ledger
from . import repository as repo
from .config import settings
from .util import utcnow

log = logging.getLogger("continuum.notify")

URGENT = {"urgent", "emergency"}


def sms_configured() -> bool:
    return bool(settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number)


def email_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send_sms(to: str, body: str) -> dict:
    """Send one SMS. Returns {ok, ref?, detail}; never raises."""
    from .scheduler import _dialable  # E.164 normalisation shared with calls

    if not sms_configured():
        return {"ok": False, "detail": "SMS is not configured (TWILIO_* settings)."}
    number = _dialable(to)
    if not number:
        return {"ok": False, "detail": "No valid mobile number."}
    try:
        response = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
            data={"To": number, "From": settings.twilio_from_number, "Body": body[:1500]},
            auth=(settings.twilio_account_sid or "", settings.twilio_auth_token or ""),
            timeout=15,
        )
    except Exception as exc:  # network error: the message did not go out
        return {"ok": False, "detail": f"SMS failed: {type(exc).__name__}"}
    if response.status_code >= 400:
        return {"ok": False, "detail": f"SMS rejected (Twilio HTTP {response.status_code})"}
    return {"ok": True, "ref": (response.json() or {}).get("sid"), "detail": "SMS sent"}


def send_email(to: str, subject: str, body: str) -> dict:
    """Send one plain-text email. Returns {ok, detail}; never raises."""
    if not email_configured():
        return {"ok": False, "detail": "Email is not configured (SMTP_* settings)."}
    if not to or "@" not in to:
        return {"ok": False, "detail": "No valid email address."}
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host or "", settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(message)
    except Exception as exc:
        return {"ok": False, "detail": f"Email failed: {type(exc).__name__}"}
    return {"ok": True, "detail": "Email sent"}


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def urgency(approval: dict) -> str | None:
    changes = approval.get("proposed_changes") or (approval.get("proposal") or {}).get("changes") or {}
    level = changes.get("urgency")
    return level if level in URGENT else None


def _ack_window(level: str) -> timedelta:
    minutes = settings.page_ack_minutes_emergency if level == "emergency" else settings.page_ack_minutes_urgent
    return timedelta(minutes=minutes)


def page_on_call(db: Database, approval: dict, attempt: int = 1) -> dict:
    """Page the on-call clinician about one escalation, by SMS and email."""
    level = urgency(approval) or "urgent"
    patient = repo.get_patient(db, approval["patient_id"]) or {}
    link = f"{settings.public_app_url.rstrip('/')}/approvals"
    # The page names the patient and the urgency, not clinical detail: SMS and
    # email are not secure channels for PHI beyond what a page needs.
    first = (patient.get("name") or "a patient").split(" ")[0]
    initial = ((patient.get("name") or " ").split(" ")[-1][:1] + ".") if patient.get("name") else ""
    text = (
        f"Continuum {level.upper()}: {first} {initial} needs clinician review"
        f"{' (repeat page)' if attempt > 1 else ''}. Open {link} and acknowledge."
    )
    results = []
    for phone in _split(settings.on_call_phone):
        results.append(("SMS", phone, send_sms(phone, text)))
    for email in _split(settings.on_call_email):
        results.append(("Email", email, send_email(email, f"[Continuum] {level.title()} escalation needs review", text)))
    if not results:
        results.append(("Page", "on-call", {"ok": False, "detail": "No on-call phone or email configured (ON_CALL_PHONE / ON_CALL_EMAIL)."}))

    sent = [r for r in results if r[2]["ok"]]
    action = ledger.intend(
        db,
        kind="clinician_page",
        patient_id=approval["patient_id"],
        summary=f"Page on-call clinician ({level}), attempt {attempt}",
        proof="clinician acknowledgment",
        actor="Continuum (paging)",
        key=f"page:{approval['id']}:{attempt}",
        obligation_id=approval.get("obligation_id"),
        confirm_within_hours=_ack_window(level).total_seconds() / 3600,
        metadata={"approval_id": approval["id"], "channels": [f"{c} {to}: {r['detail']}" for c, to, r in results]},
    )
    detail = "; ".join(f"{c}: {r['detail']}" for c, _, r in results)
    if sent:
        ledger.mark_sent(db, action["id"], external_ref=f"page:{approval['id']}", detail=detail)
    else:
        ledger.mark_failed(db, action["id"], detail)
    pages = list(approval.get("pages") or []) + [{"at": utcnow(), "attempt": attempt, "ok": bool(sent), "detail": detail}]
    status = {"pages": pages, "last_paged_at": utcnow(), "urgency": level}
    if sent:
        status.update(page_status="sent", page_failed=False)
    elif not any(p.get("ok") for p in pages):
        status.update(page_status="failed", page_failed=True,
                      page_note=f"Paging failed — the on-call clinician has NOT been notified. {detail}")
    db.approvals.update_one({"id": approval["id"]}, {"$set": status})
    if not sent and not any(p.get("ok") for p in pages):
        _flag_failed_page(db, approval, level, detail)
    repo.audit(
        db,
        patient_id=approval["patient_id"],
        actor="Continuum (paging)",
        action="Paged on-call clinician" if sent else "Page could not be sent",
        detail=detail,
        after={"approval_id": approval["id"], "attempt": attempt},
    )
    return {"ok": bool(sent), "detail": detail, "attempt": attempt}


def _flag_failed_page(db: Database, approval: dict, level: str, detail: str) -> None:
    """Put a failed page in front of the coordinators, who can phone the on-call clinician.

    Escalations sit in the clinician queue; a page that never went out means no
    clinician knows to look there, so the coordinator queue gets a task too
    (one per escalation: it dedupes on the escalation id).
    """
    patient = repo.get_patient(db, approval["patient_id"]) or {}
    name = patient.get("name") or "patient"
    try:
        repo.create_approval(
            db,
            {
                "patient_id": approval["patient_id"],
                "run_id": approval.get("run_id"),
                "thread_id": approval.get("thread_id") or approval["patient_id"],
                "kind": "follow_up",
                "mode": "queued",
                "obligation_id": f"page_failed:{approval['id']}",
                "title": f"Page failed: contact the on-call clinician about {name} ({level})",
                "rationale": (
                    f"An {level} escalation could not be paged to the on-call clinician ({detail}). "
                    "Reach the clinician directly and point them to the escalation in the clinician queue."
                ),
                "proposed_changes": {"escalation_id": approval["id"], "urgency": level, "page_detail": detail},
                "evidence_summary": approval.get("rationale") or approval.get("title") or "Escalation",
                "confidence": 1.0,
                "source": "paging",
            },
        )
    except Exception:  # flagging must never break the page bookkeeping
        log.exception("Could not flag failed page for %s", approval["id"])


def page_in_background(db: Database, approval: dict) -> None:
    """Page now without holding up the request that queued the escalation."""
    if not urgency(approval):
        return
    attempt = len(approval.get("pages") or []) + 1  # >1 when an open escalation's urgency rises
    if not (sms_configured() or email_configured()):
        page_on_call(db, approval, attempt=attempt)  # nothing to wait on: record the outcome inline
        return
    threading.Thread(target=page_on_call, args=(db, approval, attempt), daemon=True).start()


def acknowledge(db: Database, approval_id: str, *, actor: str) -> dict | None:
    approval = repo.get_approval(db, approval_id)
    if not approval:
        return None
    if approval.get("acknowledged_at"):
        return approval
    db.approvals.update_one({"id": approval_id}, {"$set": {"acknowledged_at": utcnow(), "acknowledged_by": actor}})
    ledger.confirm(db, external_ref=f"page:{approval_id}", kind="clinician_page", evidence=f"Acknowledged by {actor}")
    repo.audit(
        db,
        patient_id=approval["patient_id"],
        actor=actor,
        action="Acknowledged escalation",
        detail=approval.get("title") or "Escalation",
        after={"approval_id": approval_id},
    )
    return repo.get_approval(db, approval_id)


def repage_unacknowledged(db: Database, now: datetime | None = None) -> list[dict]:
    """Sweep step: page again when an urgent escalation sits unacknowledged."""
    now = now or datetime.now(timezone.utc)
    actions = []
    for approval in db.approvals.find({"status": "pending", "kind": "escalate", "acknowledged_at": None}, {"_id": 0}):
        level = urgency(approval)
        pages = approval.get("pages") or []
        if not level or len(pages) >= settings.page_max_attempts:
            continue
        last = pages[-1]["at"] if pages else approval.get("created_at")
        try:
            last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        except ValueError:
            continue
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        if pages and now - last_dt < _ack_window(level):
            continue
        result = page_on_call(db, approval, attempt=len(pages) + 1)
        actions.append({"type": "clinician_repaged" if pages else "clinician_paged", "approval_id": approval["id"],
                        "patient_id": approval["patient_id"], "ok": result["ok"]})
    return actions
