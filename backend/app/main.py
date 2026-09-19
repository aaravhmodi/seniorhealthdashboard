from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

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
    yield


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
