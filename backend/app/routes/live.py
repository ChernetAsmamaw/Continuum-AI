"""Live updates (change streams over SSE) and the action ledger."""

from __future__ import annotations

import asyncio
import json
import queue

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .. import ledger, streams
from ..db import get_db
from ..deps import require_auth, session_payload

router = APIRouter(tags=["live"])


@router.get("/stream")
async def stream(request: Request, token: str = Query(...)) -> StreamingResponse:
    """Server-sent events for every change on the task-state collections.

    EventSource can't send headers, so the JWT comes as ``?token=``. Only a
    signed-in staff session is accepted: scoped tokens (patient check-in
    links) are refused.
    """
    try:
        payload = session_payload(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if not get_db().users.find_one({"id": payload.get("sub")}, {"_id": 1}):
        raise HTTPException(status_code=401, detail="Unknown user")
    q = streams.subscribe()

    async def events():
        try:
            yield f"event: hello\ndata: {json.dumps({'watching': streams.WATCHED, 'running': streams.status['running']})}\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.to_thread(q.get, True, 15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"event: change\ndata: {json.dumps(event, default=str)}\n\n"
        finally:
            streams.unsubscribe(q)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/stream/status", dependencies=[Depends(require_auth)])
def stream_status() -> dict:
    return {**streams.status, "watching": streams.WATCHED}


@router.get("/actions", dependencies=[Depends(require_auth)])
def actions(patient_id: str | None = None, status: str | None = None, limit: int = 100) -> dict:
    """The action ledger: intended → sent → confirmed (or failed / unconfirmed / unknown)."""
    db = get_db()
    return {"summary": ledger.summary(db), "actions": ledger.list_actions(db, patient_id=patient_id, status=status, limit=limit)}
