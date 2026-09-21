"""Small real-server fixture used only by the SIGTERM lifecycle test."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.core.lifecycle import RequestDrainMiddleware, ServiceLifecycleState


state = ServiceLifecycleState()
marker_dir = Path(os.environ["FARMTWIN_LIFECYCLE_MARKER_DIR"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.mark_ready()
    try:
        yield
    finally:
        state.begin_shutdown()
        await state.drain(20.0)
        (marker_dir / "cleanup.json").write_text(
            json.dumps({"database_disposed": True, "identity_client_closed": True}),
            encoding="utf-8",
        )


api = FastAPI(lifespan=lifespan)


@api.get("/slow")
async def slow_request():
    (marker_dir / "request-started").write_text("started", encoding="utf-8")
    await asyncio.sleep(0.5)
    return {"completed": True}


app = RequestDrainMiddleware(api, state)
