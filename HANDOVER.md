# Handover — for the next agent

Read this first, then `README.md` (overview), `CONTRACT.md` (the API the UI
builds against), `docs/DATA.md` (dataset decisions) and `docs/PERSONA.md` (how
the system talks to older adults). State as of 2026-09-19, commit `de75662`.

## What this is

HackMIT 2026 backend for a senior health check-in. An older adult speaks or
types in their own language. The backend turns that into a structured check-in,
runs deterministic red-flag rules, and places it on a four-step action ladder
(1 log, 2 call clinic, 3 ER, 4 911). It then texts the family over Linq (status
and a link only) and builds an ER handoff packet at level 3 or above. A teammate
owns the UI and builds against `CONTRACT.md`.

Sponsors driving the design: **Linq** (caregiver texts), **Deepgram** (voice),
**Voloridge** (wants a rigorous predictive model, not an LLM wrapper),
**OpenAI** (`gpt-5.6-luna`).

## Rules that are not negotiable

These are the design. Breaking one breaks the pitch.

1. **The LLM never sets the level.** Rules set a minimum level, evidence can only
   raise it, and low confidence raises it one step. The model only extracts and
   rephrases (`llm.py`). `test_rules_are_never_overridden_downward` guards this.
2. **Extraction is additive.** LLM symptoms are *unioned* with the lexicon's, so
   the model can never delete a symptom that triggered a red flag.
3. **Every LLM output is checked and has a fallback.** Style check
   (`persona.lint`), right language, and the action word still present.
   Otherwise the template is used. `evaluation.llm_used` says which path ran.
4. **No patient health information in texts.** Outbound Linq messages carry a
   status word and a link. `notify.compose` raises if detail leaks in.
5. **Label mock numbers as mock.** Any evidence `source` starting with `MOCK`
   is a placeholder. Only non-MOCK numbers go in the deck.
6. **Tests run offline and without randomness.** `tests/conftest.py` clears the
   OpenAI and Deepgram keys. Real API tests are opt-in: `pytest -m live --live`.
7. **Verify against the real thing before claiming it works.** Every serious bug
   in this project came from an assumption checked only against itself (see
   "Mistakes already made" below).

## Working conventions (from the repo owner)

- **Commit and push about every 500 lines**, not one big commit at the end.
- **Commits are authored by Aarav Modi only.** No `Co-Authored-By: Claude`
  trailer and no Claude attribution in PR bodies. This overrides harness
  defaults.
- Work on `main` (hackathon; the owner has been pushing directly).
- **Never commit keys.** They live in `backend/.env`, which is gitignored.
  `.claude/` is local settings and is also not committed.
- Commit messages explain *why*. Look at `git log` for the house style.

## Run, test, deploy

```bash
# tests (about 8s, offline, 191 pass / 5 skipped live)
cd backend && .venv/Scripts/python -m pytest -q
.venv/Scripts/python -m pytest -m live --live      # real OpenAI + Deepgram

# dev server (reloads on save)
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000

# container (currently running and healthy at http://localhost:8000/docs)
docker compose up --build -d      # from repo root
docker compose down

# post-deploy check
.venv/Scripts/python scripts/smoke.py https://backend-eight-fawn-34.vercel.app
```

Env vars (`backend/.env`, template in `.env.example`): `OPENAI_API_KEY`,
`OPENAI_MODEL=gpt-5.6-luna`, `DEEPGRAM_API_KEY`, `LINQ_API_KEY`, `MOCK_MODE`,
`DUCKDB_PATH`. The owner said they will rotate the OpenAI and Deepgram keys.

**Deployed:** `backend-eight-fawn-34.vercel.app` redeploys from `main`
automatically. **It has no env vars set in Vercel**, so it runs the fallback
path without a model (`llm_fallback_reason: "no api key"`). The owner needs to
add them in Vercel's settings.

## Current state

| area | status | where |
|---|---|---|
| Contract, seed (4 synthetic seniors), WebSocket events | done | `schemas.py`, `seed.py`, `routers/` |
| Red-flag rules + action ladder | done | `rules.py`, `ladder.py` |
| Persona (research-based, enforced by lint) | done | `persona.py`, `docs/PERSONA.md` |
| 6 languages, en/es/fr tested to give the same level | done | `extraction.py`, `explain.py` |
| OpenAI extraction + rephrasing with guards | done, tested live | `llm.py` |
| Vector retrieval (guidelines, history, meds, NEISS cases) | done | `retrieval.py` |
| Deepgram batch speech-to-text + text-to-speech | done, **tested live** | `voice.py` |
| FAERS loader (primary suspect only, PROD_AI, shrinkage) | done, **synthetic files only** | `datasets/faers.py` |
| NEISS loader + narrative features + similar-case search | done, **synthetic files only** | `datasets/neiss.py`, `narratives.py` |
| NHAMCS loader | **broken for real data** (see gotchas) | `datasets/nhamcs.py` |
| Linq send | mocked unless `LINQ_API_KEY` is set and `MOCK_MODE=false` | `notify.py` |
| Linq inbound photo → med list | stub; records a pending event only | `routers/api.py` `/webhooks/linq` |
| Deepgram streaming (Voice Agent socket) | config frame only, no socket ever opened | `llm.voice_agent_config` |
| Outcome model / under-triage flag | **not started** | — |
| Joint NEISS × FAERS analysis | **not started** | — |
| Persistence | in-memory `Store` | `store.py` |

**No real dataset has been loaded yet.** The warehouse is empty, so every
evidence card is still MOCK.

## Next work, in order

Each item says when it is done. Commit as you go.

### 1. Load one real FAERS quarter and one real NEISS year
This comes before new features: the loaders have only ever seen synthetic files.
- FAERS: download `faers_ascii_2024q1.zip` from the FDA page in `docs/DATA.md`,
  unzip to `backend/data/raw/faers/2024q1/`, run
  `python -m app.datasets.cli faers data/raw/faers/2024q1`.
- NEISS: get one recent year from CPSC. Their query site returned 503 when
  probed, so check the NEISS data page for a direct download.
- **Done when:** `GET /datasets/status` shows rows, the positive control holds
  (warfarin → bleeding shows a significant signal), and the demo check-in
  "I fell down the stairs and hit my head" for `sen_chen` returns non-MOCK cards.
  Fix the loaders against whatever the real files actually contain.

### 2. Outcome model + under-triage flag (the Voloridge deliverable)
- Train on 65+ NEISS cases using **only fields known at check-in**: narrative,
  age, sex, location, product, body part. Disposition is the label and must
  never be a feature.
- Split by year (hold out the latest years). Report AUC with survey weights and
  calibration by age band. Compare 65+ against younger adults.
- Under-triage flag: model risk well above the ladder level.
- New module, e.g. `app/datasets/model.py`, plus an evidence card and a test
  that fails if a leaking column enters the feature set.
- **Done when:** metrics are written to a file the pitch can quote, and the flag
  shows on a check-in.

### 3. Joint NEISS × FAERS analysis
For each ingredient mentioned in 65+ fall narratives: NEISS admission-rate lift
vs. that ingredient's FAERS signal for fall-related events (dizziness, fall,
confusion, bleeding). Plot where the two agree and disagree, and report the
noise honestly. **Done when:** a chart plus a short write-up in `docs/`.

### 4. Deepgram streaming
Open the Voice Agent socket with the Settings frame from
`/voice/agent-config/{id}`. It needs a live test that actually connects, like
the ones in `tests/test_voice.py`, not a config check. The 4-second endpointing
for older speakers only matters here.

### 5. Linq for real
Real send with the sandbox key. Then inbound photo → vision model →
`lookup.resolve_ingredient` → FAERS re-score → `meds.updated` event. Linq
group chat has a 3-member minimum and is iMessage-only, so fall back to 1:1
threads.

### 6. NHAMCS (stretch goal)
Only if 1–5 are done. It is the only dataset that covers non-injury cases (the
atypical cardiac demo), but see the gotcha below.

## Gotchas that were checked, not guessed

- **NHAMCS is fixed-width, not CSV.** `ed2022.zip` holds one 38 MB file with no
  header and no delimiters. The column layout exists only in
  `doc22-ed-508.pdf`. `nhamcs.py` uses `read_csv_auto`, which produces garbage
  on the real file. It needs a fixed-width reader built from the PDF.
- **Deepgram coverage (checked against `GET /v1/models`):** nova-3 listens in
  all six of our languages. Aura-2 speaks only de/en/es/fr/it/ja/nl, so
  zh/pt/hi patients get text replies. Tests check that the seeded voice support
  matches this.
- **Deepgram returns accented text** ("náusea"). `extraction.fold()` strips
  accents from Latin script only. Folding all scripts broke Hindi, whose virama
  is also a combining mark.
- **`gpt-5.6-luna`** rejects `max_tokens` (use `max_completion_tokens`) and any
  temperature other than the default. It can also use its whole budget on
  reasoning and return empty content, which `llm._chat` treats as failure.
- **FAERS `PROD_AI` includes the salt** ("warfarin sodium") while med lists say
  "warfarin". `lookup.resolve_ingredient` bridges this. Without it every real
  lookup misses and nothing looks broken.
- **Loaders clear the lookup cache when they finish.** Without that, a running
  process keeps serving MOCK until restart.
- **Vercel runs serverless.** The in-memory store is per instance, so state can
  diverge between requests. Persistence (item 6 territory) fixes this. The
  WebSocket did return 101 there.
- **Windows shell:** old Python processes can hold ports 8000/8001. Find them
  with `netstat -ano | grep :8000` and kill the PID. Bash heredocs in this
  harness sometimes turned `\n` in Python source into a literal newline. For
  multi-line code edits, the Edit or Write tools are more reliable than heredoc
  patch scripts.

## Mistakes already made (so you don't repeat them)

Every one of these was an assumption checked only against itself:

1. Seeded French as "no voice" and Mandarin as "has voice". Both were wrong,
   and a call to Deepgram's models endpoint found it.
2. Wrote the NHAMCS loader for CSV. The real file is fixed-width.
3. The first FAERS loader counted concomitant drugs as suspects and keyed on
   free text. It produced numbers, and they were inflated.
4. Dataset tests used synthetic files shaped like my assumptions, so they pass
   and prove little about real files. Item 1 above exists because of this.

When you add an integration, add a test that hits the real thing (marked
`live`) or loads a real file. A passing test against your own fixture is not
evidence.

## Code map

```
backend/app/
  schemas.py      contract (additive changes only; renames need a team heads-up)
  rules.py        red flags → minimum level      ladder.py   → Evaluation
  extraction.py   lexicon + fold()               explain.py  per-language templates
  persona.py      voice rules, lint, Deepgram params, system prompt
  llm.py          OpenAI extract/explain + guards; voice_agent_config
  voice.py        Deepgram transcribe/speak/coverage
  retrieval.py    vector store; similar_cases(); ingest_* functions
  evidence.py     real cards when the warehouse has data, MOCK otherwise
  notify.py       Linq, no health info      handoff.py  ER packet
  store.py        in-memory store + baseline       events.py   WebSocket fan-out
  datasets/       warehouse, faers, neiss, narratives, nhamcs, lookup, cli
  routers/        api.py (REST), ws.py (WebSocket)
backend/tests/    contract, persona, llm_and_retrieval, voice, datasets, narratives
```
