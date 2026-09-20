"""In-memory store, with write-through persistence.

Sprint 0 kept everything in a dict so nobody was blocked on a database. The
interface below is still the only thing the routers touch -- and that is what
let Postgres arrive underneath it without a single call site changing.

Reads stay in the dict, at memory speed and synchronous. Writes additionally
mark the entity dirty; `persistence.flusher` pushes dirty rows to Supabase in
the background, and `persistence.hydrate` refills the dict on boot. With
Supabase unconfigured every one of those calls is a no-op and this is exactly
the dict it always was.

`_persist` is deliberately the last statement of each write method: a
persistence problem must never prevent the in-memory write that the request is
actually waiting on.
"""
from __future__ import annotations

import hashlib
import itertools
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .schemas import (
    ActionLevel,
    Alert,
    BaselineSummary,
    CarePlan,
    CareCircle,
    CheckIn,
    Evaluation,
    FollowUpJob,
    HandoffPacket,
    Senior,
    TeachBackResult,
    TimelineEntry,
)

_counters: defaultdict[str, itertools.count] = defaultdict(lambda: itertools.count(1))
_lock = threading.Lock()


def _persist(table: str, model) -> None:
    """Queue a row for the background flush. Never raises, never blocks."""
    try:
        from . import persistence

        persistence.mark(table, model)
    except Exception:  # pragma: no cover - persistence must never break a write
        pass


def stable_id(*parts: str) -> str:
    """A content-derived id that is the same in every process.

    Python's hash() is salted per process, so using it here would mint a new
    id for the same timeline entry after every restart and duplicate the row
    on re-persist. This does not.
    """
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()
    return digest[:16]


class PersistedDict(dict):
    """A dict that queues each value for persistence as it is written.

    The family-side collections are assigned into directly all over circle.py
    and followup.py (`store.alerts[a.id] = a`). Routing those through methods
    would mean finding every one and trusting the next person to remember.
    Doing it here means a write cannot be forgotten, including from code that
    does not exist yet.
    """

    def __init__(self, table: str) -> None:
        super().__init__()
        self._table = table

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        _persist(self._table, value)


def new_id(prefix: str) -> str:
    with _lock:
        return f"{prefix}_{next(_counters[prefix]):05d}"


def now() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self) -> None:
        self.seniors: dict[str, Senior] = {}
        self.checkins: dict[str, CheckIn] = {}
        self.evaluations: dict[str, Evaluation] = {}
        self.handoffs: dict[str, HandoffPacket] = {}
        self.timeline: dict[str, list[TimelineEntry]] = defaultdict(list)
        # -- Linq family side --------------------------------------------
        self.circles: dict[str, CareCircle] = PersistedDict("circles")
        self.alerts: dict[str, Alert] = PersistedDict("alerts")
        self.followups: dict[str, FollowUpJob] = PersistedDict("followups")
        self.care_plans: dict[str, CarePlan] = PersistedDict("care_plans")
        self.teachbacks: dict[str, list[TeachBackResult]] = defaultdict(list)
        # Local Linq demo reminders. Production can move these rows to the
        # same persistence table without changing the webhook contract.
        self.reminders: dict[str, dict] = PersistedDict("reminders")

    # -- seniors ----------------------------------------------------------
    def put_senior(self, senior: Senior) -> Senior:
        self.seniors[senior.id] = senior
        _persist("seniors", senior)
        return senior

    def get_senior(self, senior_id: str) -> Senior | None:
        return self.seniors.get(senior_id)

    def list_seniors(self) -> list[Senior]:
        return list(self.seniors.values())

    def caregiver_by_phone(self, phone: str):
        for senior in self.seniors.values():
            for cg in senior.caregivers:
                if cg.phone_e164 == phone:
                    return senior, cg
        return None, None

    def caregiver_by_thread(self, thread_id: str):
        for senior in self.seniors.values():
            for cg in senior.caregivers:
                if cg.linq_thread_id == thread_id:
                    return senior, cg
        return None, None

    def senior_by_chat(self, chat_id: str) -> Senior | None:
        """Which senior a Linq group chat belongs to.

        Inbound webhooks arrive with a chat id and a sender handle; the chat is
        the reliable half, because a caregiver may text from a second device.
        """
        for senior_id, circle in self.circles.items():
            if circle.chat_id and circle.chat_id == chat_id:
                return self.seniors.get(senior_id)
        return None

    # -- check-ins --------------------------------------------------------
    def put_checkin(self, checkin: CheckIn) -> CheckIn:
        self.checkins[checkin.id] = checkin
        _persist("checkins", checkin)
        return checkin

    def checkins_for(self, senior_id: str, limit: int | None = None) -> list[CheckIn]:
        rows = sorted(
            (c for c in self.checkins.values() if c.senior_id == senior_id),
            key=lambda c: c.created_at,
            reverse=True,
        )
        return rows[:limit] if limit else rows

    # -- evaluations ------------------------------------------------------
    def put_evaluation(self, ev: Evaluation) -> Evaluation:
        self.evaluations[ev.id] = ev
        _persist("evaluations", ev)
        return ev

    def evaluation_for_checkin(self, checkin_id: str) -> Evaluation | None:
        for ev in self.evaluations.values():
            if ev.checkin_id == checkin_id:
                return ev
        return None

    def latest_evaluation(self, senior_id: str) -> Evaluation | None:
        rows = sorted(
            (e for e in self.evaluations.values() if e.senior_id == senior_id),
            key=lambda e: e.created_at,
        )
        return rows[-1] if rows else None

    # -- timeline ---------------------------------------------------------
    def add_timeline(self, senior_id: str, entry: TimelineEntry) -> None:
        self.timeline[senior_id].append(entry)
        # A timeline entry has no id of its own, so one is minted here. It has
        # to be stable across a replay of the same entry, or a restart would
        # duplicate the row -- hence the content, not a counter.
        row_id = stable_id(senior_id, entry.at.isoformat(), entry.summary)
        _persist("timeline", (row_id, senior_id, entry))

    def timeline_for(self, senior_id: str) -> list[TimelineEntry]:
        return sorted(self.timeline[senior_id], key=lambda e: e.at, reverse=True)

    # -- derived ----------------------------------------------------------
    def baseline(self, senior_id: str, window_days: int = 14) -> BaselineSummary:
        """This senior's own normal, and what is drifting away from it.

        Trend is a crude split-half comparison over the window -- deliberately
        simple, and replaced by the real baseline service in Sprint 2.
        """
        cutoff = now() - timedelta(days=window_days)
        rows = [c for c in self.checkins_for(senior_id) if c.created_at >= cutoff]
        freq: defaultdict[str, int] = defaultdict(int)
        for c in rows:
            for s in c.symptoms:
                freq[s.label] += 1

        levels = [
            int(self.evaluation_for_checkin(c.id).level)
            for c in rows
            if self.evaluation_for_checkin(c.id)
        ]
        mean_level = sum(levels) / len(levels) if levels else 1.0

        midpoint = now() - timedelta(days=window_days / 2)
        recent: defaultdict[str, int] = defaultdict(int)
        older: defaultdict[str, int] = defaultdict(int)
        for c in rows:
            bucket = recent if c.created_at >= midpoint else older
            for s in c.symptoms:
                bucket[s.label] += 1

        up = sorted(k for k in freq if recent[k] > older[k] + 1)
        down = sorted(k for k in freq if older[k] > recent[k] + 1)

        return BaselineSummary(
            window_days=window_days,
            checkin_count=len(rows),
            symptom_frequency=dict(sorted(freq.items(), key=lambda kv: -kv[1])),
            mean_level=round(mean_level, 2),
            trending_up=up,
            trending_down=down,
        )


store = Store()
