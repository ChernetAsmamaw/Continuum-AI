"""Public endpoints for patients: the check-in page and inbound SMS.

No console session here. The check-in page is gated by a signed, scoped,
expiring link plus a second identifier (date of birth); inbound SMS by
Twilio's request signature.
"""

from __future__ import annotations

from html import escape
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import messaging
from ..db import demo_today

router = APIRouter(tags=["patient"])


class VerifyRequest(BaseModel):
    token: str
    answer: str = Field(min_length=1, max_length=40)


class PortalUpdate(BaseModel):
    obligation_id: str
    status: str
    date: str | None = None


class SubmitRequest(BaseModel):
    session: str
    updates: list[PortalUpdate] = Field(default_factory=list, max_length=30)
    message: str | None = Field(default=None, max_length=2000)
    callback: bool = False


def _wrap(call):
    try:
        return call()
    except messaging.PortalError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/portal/checkin/{token}")
def open_checkin(token: str) -> dict:
    return _wrap(lambda: messaging.open_link(token))


@router.post("/portal/checkin/verify")
def verify(payload: VerifyRequest) -> dict:
    return _wrap(lambda: messaging.verify(payload.token, payload.answer, demo_today()))


@router.post("/portal/checkin/submit")
def submit(payload: SubmitRequest) -> dict:
    return _wrap(
        lambda: messaging.submit(
            payload.session, [u.model_dump() for u in payload.updates], payload.message, payload.callback, demo_today()
        )
    )


@router.post("/messages/sms/inbound")
async def inbound_sms(request: Request) -> Response:
    # Twilio posts application/x-www-form-urlencoded; parsed here so the API
    # needs no multipart dependency.
    params = dict(parse_qsl((await request.body()).decode("utf-8", "replace"), keep_blank_values=True))
    if not messaging.twilio_signature_ok(str(request.url), params, request.headers.get("X-Twilio-Signature")):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    result = messaging.inbound_sms(params.get("From", ""), params.get("Body", ""), params.get("MessageSid"), demo_today())
    reply = result.get("reply")
    body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response>"
    if reply:
        body += f"<Message>{escape(reply)}</Message>"
    body += "</Response>"
    return Response(content=body, media_type="application/xml")
