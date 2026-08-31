"""Server-Sent Events stream."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from app.deps import db, event_bus

router = APIRouter(prefix="/api", tags=["stream"])


@router.get("/stream")
async def stream_events() -> StreamingResponse:
    queue = event_bus.subscribe()

    async def generator():
        try:
            status = await db.get_status()
            yield f"data: {json.dumps({'type': 'status', 'data': status})}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        finally:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
