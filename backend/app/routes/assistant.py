"""Console assistant: streamed answers with tool results the console renders."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool

from .. import assistant
from ..deps import require_auth

router = APIRouter(prefix="/assistant", tags=["assistant"])


class ChatMessage(BaseModel):
    role: str
    content: str = Field(default="", max_length=8000)


class AssistantRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    context: dict | None = None


@router.post("/chat")
async def chat(payload: AssistantRequest, user: dict = Depends(require_auth)) -> StreamingResponse:
    events = assistant.stream([m.model_dump() for m in payload.messages], payload.context, user)

    async def sse():
        async for event in iterate_in_threadpool(events):
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
