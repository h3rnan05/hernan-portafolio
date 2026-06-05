"""AI assistant chat endpoint — streams Claude responses as Server-Sent Events."""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.ai.brain import stream_chat
from app.config import get_settings
from app.db import AsyncSessionLocal

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str  # 'user' | 'assistant'
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(default_factory=list)


@router.get("/health")
async def chat_health() -> dict:
    """Lets the frontend know whether the assistant is configured."""
    return {"enabled": bool(get_settings().anthropic_api_key)}


@router.post("")
async def chat(req: ChatRequest) -> StreamingResponse:
    """Stream one assistant turn as SSE.

    The DB session is opened inside the generator so it stays alive for the
    whole stream (a Depends-injected session would be torn down before the
    streaming body runs).
    """
    history = [m.model_dump() for m in req.messages]

    async def gen():
        async with AsyncSessionLocal() as session:
            async for event in stream_chat(session, history):
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable proxy buffering for live tokens
        },
    )
