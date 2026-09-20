"""Supabase Postgres, behind the in-memory store rather than in front of it.

The problem this solves is narrow and real: Render's free tier sleeps after
about fifteen minutes idle, and everything the family side knows lives in a
dict. On wake, every care circle, every unacknowledged alert and every
scheduled follow-up is gone. An alert with an escalation clock still running
is the worst of these -- lose it and a family that never answered is never
escalated to, and nobody ever finds out.

**Write-through, not read-through.** The dict stays the read path, so every
existing synchronous call site (`store.baseline`, `store.checkins_for`, the
routers) keeps working unchanged and at memory speed. Writes mark the entity
dirty; a background flusher pushes dirty rows to Supabase. On boot we hydrate
the dict back from Postgres.

That ordering is the whole design. Making the store async would have meant
touching every call site in the app for a durability guarantee that a
hackathon demo needs at exactly one moment -- process restart -- and a flush
loop buys that without a rewrite.

**It degrades to nothing.** With `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`
unset, every function here is a no-op and the app behaves exactly as it did
before. Local dev and the whole test suite run that way.

The service-role key bypasses RLS and must never reach a browser. It is a
backend-only secret; the frontend keeps using the publishable key for auth.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

import httpx
from pydantic import BaseModel

from .config import get_settings

log = logging.getLogger("carepath.persistence")

# table -> how to turn one model into a row, and back
SCHEMA = "carepath"


class Table:
    """One persisted collection: how a model becomes a row and returns."""

    def __init__(
        self,
        name: str,
        key: str,
        to_row: Callable[[Any], dict[str, Any]],
        model: type[BaseModel] | None = None,
    ) -> None:
        self.name = name
        self.key = key
        self.to_row = to_row
        self.model = model


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _dump(model: BaseModel) -> dict[str, Any]:
    """JSON-safe dict. mode="json" turns enums, dates and UUIDs into
    primitives, which is what jsonb wants and what pydantic will read back."""
    return model.model_dump(mode="json")


TABLES: dict[str, Table] = {
    "seniors": Table(
        "seniors", "id",
        lambda m: {"id": m.id, "data": _dump(m)},
    ),
    "checkins": Table(
        "checkins", "id",
        lambda m: {"id": m.id, "senior_id": m.senior_id,
                   "created_at": _iso(m.created_at), "data": _dump(m)},
    ),
    "evaluations": Table(
        "evaluations", "id",
        lambda m: {"id": m.id, "senior_id": m.senior_id, "checkin_id": m.checkin_id,
                   "created_at": _iso(m.created_at), "data": _dump(m)},
    ),
    "circles": Table(
        "circles", "senior_id",
        lambda m: {"senior_id": m.senior_id, "chat_id": m.chat_id, "data": _dump(m)},
    ),
    "alerts": Table(
        "alerts", "id",
        lambda m: {"id": m.id, "senior_id": m.senior_id,
                   "created_at": _iso(m.created_at), "resolved": m.resolved,
                   "data": _dump(m)},
    ),
    "followups": Table(
        "followups", "id",
        lambda m: {"id": m.id, "senior_id": m.senior_id, "due_at": _iso(m.due_at),
                   "status": m.status, "data": _dump(m)},
    ),
    "care_plans": Table(
        "care_plans", "senior_id",
        lambda m: {"senior_id": m.senior_id, "data": _dump(m)},
    ),
    "timeline": Table(
        "timeline", "id",
        lambda m: {"id": m[0], "senior_id": m[1], "at": _iso(m[2].at),
                   "data": _dump(m[2])},
    ),
    "teachbacks": Table(
        "teachbacks", "id",
        lambda m: {"id": m[0], "senior_id": m[1].senior_id, "at": _iso(m[1].at),
                   "data": _dump(m[1])},
    ),
}


# --------------------------------------------------------------------------
# Dirty tracking
# --------------------------------------------------------------------------
# table -> {key: row}. Last write wins, which is correct: these are whole-row
# upserts of the current state, not a change log.
_dirty: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in TABLES}
_dirty_lock = threading.Lock()


def enabled() -> bool:
    settings = get_settings()
    return bool(settings.supabase_url and settings.supabase_service_role_key)


def mark(table: str, model: Any) -> None:
    """Queue one entity for the next flush. Cheap, synchronous, never raises.

    Called from the store's write methods, which are on the request path, so
    this must not touch the network and must not be able to fail a check-in.
    """
    if not enabled():
        return
    spec = TABLES.get(table)
    if not spec:
        return
    try:
        row = spec.to_row(model)
    except Exception:
        log.exception("could not serialise a %s row; not persisting it", table)
        return
    with _dirty_lock:
        _dirty[table][str(row[spec.key])] = row


def pending() -> int:
    with _dirty_lock:
        return sum(len(rows) for rows in _dirty.values())


def _take() -> dict[str, list[dict[str, Any]]]:
    with _dirty_lock:
        batch = {name: list(rows.values()) for name, rows in _dirty.items() if rows}
        for name in batch:
            _dirty[name].clear()
    return batch


def _restore(batch: dict[str, list[dict[str, Any]]]) -> None:
    """Put a failed batch back, without clobbering anything written since."""
    with _dirty_lock:
        for name, rows in batch.items():
            spec = TABLES[name]
            for row in rows:
                _dirty[name].setdefault(str(row[spec.key]), row)


# --------------------------------------------------------------------------
# PostgREST
# --------------------------------------------------------------------------
def _headers() -> dict[str, str]:
    key = get_settings().supabase_service_role_key
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept-Profile": SCHEMA,
        "Content-Profile": SCHEMA,
    }


def _url(table: str) -> str:
    base = get_settings().supabase_url.rstrip("/")
    return f"{base}/rest/v1/{table}"


async def flush() -> dict[str, int]:
    """Push every dirty row. Returns what was written, per table.

    A failed table is put back on the queue and retried on the next tick
    rather than dropped -- the whole point is that the state outlives things
    going wrong, and silently losing a write would defeat that.
    """
    if not enabled():
        return {}
    batch = _take()
    if not batch:
        return {}

    written: dict[str, int] = {}
    failed: dict[str, list[dict[str, Any]]] = {}
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=settings.supabase_timeout_s) as client:
            for table, rows in batch.items():
                spec = TABLES[table]
                try:
                    resp = await client.post(
                        _url(table),
                        headers={
                            **_headers(),
                            # Upsert on the primary key; we are writing current
                            # state, so a row we have seen before is an update.
                            "Prefer": "resolution=merge-duplicates,return=minimal",
                        },
                        params={"on_conflict": spec.key},
                        json=rows,
                    )
                    if resp.status_code >= 400:
                        log.error(
                            "supabase upsert %s failed (%s): %s",
                            table, resp.status_code, resp.text[:300],
                        )
                        failed[table] = rows
                    else:
                        written[table] = len(rows)
                except Exception:
                    log.exception("supabase upsert %s raised", table)
                    failed[table] = rows
    except Exception:
        log.exception("supabase flush could not start")
        failed = batch

    if failed:
        _restore(failed)
    return written


async def _select(client: httpx.AsyncClient, table: str) -> list[dict[str, Any]]:
    resp = await client.get(
        _url(table), headers=_headers(), params={"select": "*", "limit": 5000}
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows if isinstance(rows, list) else []


async def hydrate(store: Any) -> dict[str, int]:
    """Refill the in-memory store from Postgres on boot.

    Anything already seeded is kept; persisted rows win on a key collision,
    because a real circle opened yesterday beats a seed fixture.
    """
    if not enabled():
        return {}

    from .schemas import (
        Alert, CarePlan, CareCircle, CheckIn, Evaluation, FollowUpJob,
        Senior, TeachBackResult, TimelineEntry,
    )

    loaded: dict[str, int] = {}
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=settings.supabase_timeout_s) as client:
            for table, model, put in (
                ("seniors", Senior, lambda m: store.seniors.__setitem__(m.id, m)),
                ("checkins", CheckIn, lambda m: store.checkins.__setitem__(m.id, m)),
                ("evaluations", Evaluation, lambda m: store.evaluations.__setitem__(m.id, m)),
                ("circles", CareCircle, lambda m: store.circles.__setitem__(m.senior_id, m)),
                ("alerts", Alert, lambda m: store.alerts.__setitem__(m.id, m)),
                ("followups", FollowUpJob, lambda m: store.followups.__setitem__(m.id, m)),
                ("care_plans", CarePlan, lambda m: store.care_plans.__setitem__(m.senior_id, m)),
            ):
                try:
                    rows = await _select(client, table)
                except Exception:
                    log.exception("supabase hydrate %s failed", table)
                    continue
                count = 0
                for row in rows:
                    try:
                        put(model(**row["data"]))
                        count += 1
                    except Exception:
                        # One malformed row must not stop the rest loading.
                        log.warning("skipping unreadable %s row %s", table, row.get("id"))
                loaded[table] = count

            # The two collections keyed by senior rather than by id.
            try:
                for row in await _select(client, "timeline"):
                    try:
                        store.timeline[row["senior_id"]].append(TimelineEntry(**row["data"]))
                        loaded["timeline"] = loaded.get("timeline", 0) + 1
                    except Exception:
                        log.warning("skipping unreadable timeline row %s", row.get("id"))
            except Exception:
                log.exception("supabase hydrate timeline failed")

            try:
                for row in await _select(client, "teachbacks"):
                    try:
                        store.teachbacks[row["senior_id"]].append(
                            TeachBackResult(**row["data"])
                        )
                        loaded["teachbacks"] = loaded.get("teachbacks", 0) + 1
                    except Exception:
                        log.warning("skipping unreadable teachback row %s", row.get("id"))
            except Exception:
                log.exception("supabase hydrate teachbacks failed")
    except Exception:
        log.exception("supabase hydrate could not start")
        return loaded

    if loaded:
        log.info("hydrated from supabase: %s", loaded)
    return loaded


async def flusher(interval_s: float = 3.0) -> None:
    """Background loop. Started in the lifespan alongside the scheduler."""
    if not enabled():
        log.info("supabase not configured; state is in memory only")
        return
    while True:
        try:
            written = await flush()
            if written:
                log.debug("persisted %s", written)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("persistence flush failed")
        await asyncio.sleep(interval_s)


async def wipe() -> None:
    """Delete everything. Used by /demo/reset so a reset is a real reset."""
    if not enabled():
        return
    with _dirty_lock:
        for rows in _dirty.values():
            rows.clear()
    try:
        async with httpx.AsyncClient(timeout=get_settings().supabase_timeout_s) as client:
            for table in TABLES:
                spec = TABLES[table]
                try:
                    await client.delete(
                        _url(table), headers=_headers(),
                        params={spec.key: "not.is.null"},
                    )
                except Exception:
                    log.exception("supabase wipe %s failed", table)
    except Exception:
        log.exception("supabase wipe could not start")


def status() -> dict[str, Any]:
    settings = get_settings()
    return {
        "enabled": enabled(),
        "url_configured": bool(settings.supabase_url),
        "service_key_configured": bool(settings.supabase_service_role_key),
        "schema": SCHEMA,
        "pending_writes": pending(),
        "tables": sorted(TABLES),
    }
