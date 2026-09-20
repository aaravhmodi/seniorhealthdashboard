from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from . import followup, linq, linq_events
from .config import get_settings
from .routers import api, ws
from .schemas import CONTRACT_VERSION
from .seed import seed
from .store import store

DESCRIPTION = """
Backend for the senior check-in -> action ladder -> caregiver -> ED handoff flow.

**Sprint 0 status:** the contract below is frozen and the server is live, but the
clinical intelligence is deterministic stand-in logic:

* symptom extraction is a lexicon, not an LLM (Sprint 1)
* evidence numbers are placeholders marked `MOCK` in `source` (Sprint 2)
* Linq sends are mocked unless `LINQ_API_KEY` is set and `MOCK_MODE=false`

Everything the UI touches -- paths, field names, enums, event types -- is real.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed(store)
    # Index on boot so the first voice session already has context.
    api.retrieval_reindex()

    # The follow-up and escalation clock. One task, cancelled on shutdown, so
    # a reload does not leave a second one sending duplicate texts.
    scheduler = asyncio.create_task(followup.scheduler())

    # Tell Linq where to deliver tapbacks and replies. Best effort: a failure
    # here means inbound stops working, not that the app stops booting.
    settings = get_settings()
    if linq.is_enabled() and settings.public_api_base:
        await linq.ensure_webhook(
            f"{settings.public_api_base.rstrip('/')}/webhooks/linq",
            linq_events.SUBSCRIBED_EVENTS,
        )

    try:
        yield
    finally:
        scheduler.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler


app = FastAPI(
    title="Senior Check-in API",
    version=CONTRACT_VERSION,
    description=DESCRIPTION,
    lifespan=lifespan,
)

# Hackathon posture: any origin. Lock this down before anything real ships.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api.router)
app.include_router(ws.router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/contract", tags=["meta"])
def contract() -> dict:
    """One place for the UI to confirm what it is building against."""
    settings = get_settings()
    return {
        "contract_version": CONTRACT_VERSION,
        "mock_mode": settings.mock_mode,
        "openapi": "/openapi.json",
        "websocket": "/events",
        "demo_scenarios": "/demo/scenarios",
        "action_levels": {
            1: "log and monitor",
            2: "call clinic or pharmacist today",
            3: "go to the emergency department",
            4: "call 911 now",
        },
    }
