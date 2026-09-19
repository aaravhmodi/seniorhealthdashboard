"""Vector retrieval: the context the LLM and the voice agent are allowed to use.

Design rule that everything here exists to serve: **the model may only say what
retrieval handed it.** No recalled medical facts, no invented statistics. If a
claim is not in a chunk, it does not reach the patient. That is what makes the
"evidence-backed" line in the pitch true rather than decorative.

What goes in the index (four kinds, deliberately separate):

  guideline        short, quotable care guidance -- "when a fall needs imaging"
  cohort_stat      a NEISS/FAERS number with its n and interval attached
  patient_history  this senior's own check-ins, so the agent can say "you told
                   me the same thing on Tuesday" without hallucinating it
  med_info         plain-language notes about a medicine they actually take
  similar_case     a real de-identified NEISS injury case with its outcome,
                   so "patients who described this ended up admitted" is a
                   retrieved fact rather than a generated one

Backends: `InMemoryVectorStore` is the default and needs nothing installed --
it is enough for the hackathon's few thousand chunks. Swap in Elastic or
pgvector later by implementing the same two methods; nothing else changes.

Embeddings: OpenAI `text-embedding-3-small` when a key is present, otherwise a
deterministic hashed bag-of-words. The fallback is genuinely worse at synonyms,
but it keeps the demo alive on conference wifi, and the interface is identical.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Protocol

import httpx

from .config import get_settings

ChunkKind = Literal[
    "guideline", "cohort_stat", "patient_history", "med_info", "similar_case"
]

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 256          # hashed fallback dimension; OpenAI vectors are longer
DEFAULT_TOP_K = 5
CONTEXT_CHAR_BUDGET = 2400


@dataclass
class Chunk:
    id: str
    text: str
    kind: ChunkKind
    source: str                       # human-readable citation
    language: str = "en"
    senior_id: str | None = None      # set for patient_history; scopes the search
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None

    def cite(self) -> str:
        return f"[{self.kind}: {self.source}]"


# --------------------------------------------------------------------------
# Embeddings
# --------------------------------------------------------------------------
_TOKEN = re.compile(r"[a-z0-9À-ɏ一-鿿]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def hash_embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic, offline, no dependencies. Cosine-comparable."""
    vec = [0.0] * dim
    for token in _tokens(text):
        h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
        vec[h % dim] += 1.0
        # A second, sign-carrying slot reduces collisions between common words.
        vec[(h >> 17) % dim] += 1.0 if (h >> 1) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    name = "hash-blake2b-256"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embed(t) for t in texts]


class OpenAIEmbedder:
    name = EMBED_MODEL

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._fallback = HashEmbedder()

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={"model": EMBED_MODEL, "input": texts},
                )
                resp.raise_for_status()
                rows = sorted(resp.json()["data"], key=lambda d: d["index"])
                return [r["embedding"] for r in rows]
        except Exception:
            # Never let the retrieval layer take the app down mid-demo.
            return self._fallback.embed(texts)


def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.openai_api_key:
        return OpenAIEmbedder(settings.openai_api_key)
    return HashEmbedder()


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))  # both sides are normalized


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
class VectorStore(Protocol):
    def upsert(self, chunks: Iterable[Chunk]) -> int: ...
    def search(
        self,
        query: str,
        k: int = DEFAULT_TOP_K,
        kinds: tuple[ChunkKind, ...] | None = None,
        senior_id: str | None = None,
        language: str | None = None,
    ) -> list[tuple[Chunk, float]]: ...


class InMemoryVectorStore:
    """Exhaustive cosine search. Fine to a few tens of thousands of chunks."""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder
        self.chunks: dict[str, Chunk] = {}

    @property
    def embedder(self) -> Embedder:
        """Resolved on first use, not at import.

        Settings are not loaded yet when this module is imported, so choosing
        the embedder eagerly would pin the wrong one -- and in tests it would
        quietly start calling the real embeddings API.
        """
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    def upsert(self, chunks: Iterable[Chunk]) -> int:
        batch = [c for c in chunks]
        missing = [c for c in batch if c.embedding is None]
        if missing:
            for chunk, vector in zip(missing, self.embedder.embed([c.text for c in missing])):
                chunk.embedding = vector
        for chunk in batch:
            self.chunks[chunk.id] = chunk
        return len(batch)

    def search(
        self,
        query: str,
        k: int = DEFAULT_TOP_K,
        kinds: tuple[ChunkKind, ...] | None = None,
        senior_id: str | None = None,
        language: str | None = None,
    ) -> list[tuple[Chunk, float]]:
        if not self.chunks:
            return []
        qv = self.embedder.embed([query])[0]
        scored: list[tuple[Chunk, float]] = []
        for chunk in self.chunks.values():
            if kinds and chunk.kind not in kinds:
                continue
            # Patient history is private: it is only ever returned for its owner.
            if chunk.senior_id and chunk.senior_id != senior_id:
                continue
            if language and chunk.language not in (language, "en"):
                continue
            if chunk.embedding is None:
                continue
            scored.append((chunk, cosine(qv, chunk.embedding)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    def clear(self) -> None:
        self.chunks.clear()

    def reset(self) -> None:
        """Drop the chunks and the embedder choice. Used between tests."""
        self.chunks.clear()
        self._embedder = None


store = InMemoryVectorStore()


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
def ingest_patient_history(senior, checkins) -> int:
    """This senior's own check-ins, one chunk each.

    Lets the agent say "you mentioned dizziness three times this week" from a
    retrieved record instead of from a summary it made up.
    """
    chunks = []
    for c in checkins:
        symptoms = ", ".join(s.label for s in c.symptoms) or "nothing reported"
        chunks.append(
            Chunk(
                id=f"hist_{c.id}",
                text=(
                    f"On {c.created_at:%A %d %B}, {senior.display_name.split()[0]} "
                    f"reported: {symptoms}. They said: {c.raw_text or ''}"
                ),
                kind="patient_history",
                source=f"check-in {c.id}, {c.created_at:%Y-%m-%d}",
                language=c.language,
                senior_id=senior.id,
                metadata={"level_symptoms": symptoms, "checkin_id": c.id},
            )
        )
    return store.upsert(chunks)


def ingest_medications(senior) -> int:
    chunks = [
        Chunk(
            id=f"med_{senior.id}_{m.id}",
            text=(
                f"{senior.display_name.split()[0]} takes {m.name}"
                f"{' ' + m.dose if m.dose else ''}"
                f"{', ' + m.schedule if m.schedule else ''}."
            ),
            kind="med_info",
            source=f"medication list for {senior.id}",
            senior_id=senior.id,
            metadata={"ingredient": m.ingredient or m.name},
        )
        for m in senior.medications
    ]
    return store.upsert(chunks)


def ingest_cohort_stats(rows: Iterable[dict]) -> int:
    """Rows from the NEISS/FAERS pipelines.

    Each row must carry its own n and interval; a statistic without them is not
    allowed into the index, because the agent would then quote a bare number.
    """
    chunks = []
    for row in rows:
        if not row.get("n"):
            continue
        chunks.append(
            Chunk(
                id=f"stat_{row['id']}",
                text=row["text"],
                kind="cohort_stat",
                source=row["source"],
                metadata={k: v for k, v in row.items() if k not in ("text", "source")},
            )
        )
    return store.upsert(chunks)


def ingest_neiss_narratives(rows: Iterable[dict]) -> int:
    """Embed real senior injury cases so we can retrieve ones like this patient.

    Two decisions worth knowing:

    **Expansion.** The narrative is expanded from NEISS shorthand first: "80YOF
    GLF STRUCK HEAD" embeds near nothing, while "80 year old woman ground level
    fall struck head" embeds near what a patient actually says. That is the
    difference between retrieval that works and retrieval that returns noise.

    **Grouping.** Identical narratives are collapsed into one chunk carrying a
    case count. NEISS shorthand repeats heavily -- one sentence can be thousands
    of real cases -- and indexing each separately means the top-k is thirty
    copies of the same line while genuinely different cases never surface. The
    count is not lost: it rides in metadata and weights the outcome share.

    The outcome is deliberately kept out of the embedded text. Otherwise
    "admitted" pulls every query toward admitted cases and the rate we report
    becomes circular.
    """
    from .datasets.narratives import expand

    grouped: dict[str, dict] = {}
    for row in rows:
        readable = expand(row.get("narrative"))
        if not readable:
            continue
        flags = [
            label
            for label, present in (
                ("struck their head", row.get("head_strike")),
                ("lost consciousness", row.get("loss_of_consciousness")),
                ("taking a blood thinner", row.get("on_anticoagulant")),
            )
            if present
        ]
        text = (
            f"{readable} "
            f"({row.get('age_band', 'older adult')}, "
            f"mechanism: {row.get('mechanism', 'unspecified')}"
            + (f", {', '.join(flags)}" if flags else "")
            + ")"
        )
        entry = grouped.setdefault(
            text,
            {
                "cases": 0,
                "admitted_cases": 0,
                "mechanism": row.get("mechanism"),
                "head_strike": bool(row.get("head_strike")),
                "on_anticoagulant": bool(row.get("on_anticoagulant")),
                "age_band": row.get("age_band"),
                "case_id": row["case_id"],
            },
        )
        entry["cases"] += 1
        entry["admitted_cases"] += 1 if row.get("admitted") else 0

    chunks = [
        Chunk(
            id=f"neiss_{entry['case_id']}",
            text=text,
            kind="similar_case",
            source=(
                f"NEISS, {entry['cases']} case"
                f"{'s' if entry['cases'] != 1 else ''} like this, "
                f"ages {entry.get('age_band', '65+')}"
            ),
            metadata=entry,
        )
        for text, entry in grouped.items()
    ]
    return store.upsert(chunks)


def similar_cases(query: str, k: int = 5) -> dict:
    """Retrieve like cases and report how they ended.

    The admitted share is over the *retrieved* cases, weighted by how many real
    cases each group represents. That is a different and more honest number
    than the cohort base rate: it answers "cases that read like this one", not
    "all falls". Quote the cohort rate from the evidence card for the latter.
    """
    results = store.search(query, k=k, kinds=("similar_case",))
    cases = [
        {
            "text": chunk.text,
            "score": round(score, 4),
            "source": chunk.source,
            "cases": chunk.metadata.get("cases", 1),
            "admitted_cases": chunk.metadata.get("admitted_cases", 0),
            "mechanism": chunk.metadata.get("mechanism"),
        }
        for chunk, score in results
    ]
    total = sum(c["cases"] for c in cases)
    admitted = sum(c["admitted_cases"] for c in cases)
    return {
        "query": query,
        "matched": len(cases),
        "cases_represented": total,
        "admitted": admitted,
        "admitted_share": round(admitted / total, 2) if total else None,
        "cases": cases,
    }


def ingest_guidelines(rows: Iterable[dict]) -> int:
    return store.upsert(
        Chunk(
            id=f"guide_{row['id']}",
            text=row["text"],
            kind="guideline",
            source=row["source"],
            language=row.get("language", "en"),
            metadata=row.get("metadata", {}),
        )
        for row in rows
    )


# --------------------------------------------------------------------------
# Context assembly
# --------------------------------------------------------------------------
@dataclass
class RetrievedContext:
    query: str
    chunks: list[Chunk]
    scores: list[float]

    @property
    def citations(self) -> list[str]:
        return [c.cite() for c in self.chunks]

    def as_prompt_block(self, budget: int = CONTEXT_CHAR_BUDGET) -> str:
        """Numbered so the model can cite, truncated so it stays cheap."""
        lines, used = [], 0
        for i, chunk in enumerate(self.chunks, 1):
            line = f"[{i}] ({chunk.kind}) {chunk.text}  -- source: {chunk.source}"
            if used + len(line) > budget:
                break
            lines.append(line)
            used += len(line)
        return "\n".join(lines) if lines else "(no context retrieved)"

    def is_empty(self) -> bool:
        return not self.chunks


def build_context(
    query: str,
    senior_id: str | None = None,
    language: str = "en",
    k: int = DEFAULT_TOP_K,
) -> RetrievedContext:
    """What the LLM (and the Deepgram voice agent) may draw on for one turn."""
    results = store.search(query, k=k, senior_id=senior_id, language=language)
    return RetrievedContext(
        query=query,
        chunks=[c for c, _ in results],
        scores=[s for _, s in results],
    )
