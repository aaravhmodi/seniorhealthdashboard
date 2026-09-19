# Senior check-in backend

Voice or text check-in from an older adult, in their language, turned into a
structured record, run through a deterministic safety layer, placed on a
four-rung action ladder, pushed to the family over Linq, and handed to the ED as
a pre-arrival packet when it matters.

**Sprint 0 and the Sprint 1 spine are running.** The contract is frozen, the
server is live, the demo path works end to end in English, Spanish and French,
and 124 tests guard it (under 3 seconds, no network).

## Run it

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Then open <http://localhost:8000/docs>. It seeds three synthetic seniors on
startup, so the dashboard has data on first load.

```bash
curl -X POST localhost:8000/checkins -H 'content-type: application/json' -d '{
  "senior_id":"sen_rosa","source":"voice","language":"es",
  "text":"Me falta el aire y tengo nausea, y estoy sudando frio.",
  "transcript_confidence":0.93}'
```

That returns level 3, the `cardiac_acs` red flag, a Spanish explanation, and a
PHI-free text for the daughter. `GET /demo/scenarios` has the rest of the
scripted demo inputs with their expected levels.

## What is real and what is a stand-in

| piece | status | notes |
|---|---|---|
| red-flag rules, action ladder | **real** | the safety layer; never overridden by a model |
| symptom extraction | **real** | lexicon (6 languages) unioned with an LLM pass |
| explanations | **real** | templates in 6 languages; LLM rephrases when a key is set |
| senior voice persona | **real** | `persona.py`, enforced by `lint()` in tests |
| baseline (own history) | **real** | split-half trend over 14 days |
| vector retrieval | **real** | OpenAI embeddings, hashed offline fallback |
| OpenAI | **wired** | `gpt-5.6-luna`; falls back to templates without a key |
| dataset pipelines | **written and tested** | run the loaders (docs/DATA.md) to replace the MOCK cards |
| NHAMCS / NEISS / FAERS numbers | placeholder until loaded | `source` starts with `MOCK` |
| Linq send | mocked unless `LINQ_API_KEY` set and `MOCK_MODE=false` | |
| Deepgram | config endpoint ready; `audio_url` returns 501 | streaming is the next piece |
| storage | in memory, behind a `Store` interface | swap to DuckDB/Postgres |

## Languages

English, Spanish, French, Chinese, Portuguese and Hindi for intake and
explanations. Parity is tested: the same complaint must reach the same rung in
English, Spanish and French, because the clinical answer cannot depend on which
language you speak.

Deepgram listens in far more languages than it speaks, so patients in a language
with no confirmed voice are marked `voice_output_supported: false` and get text.
Henriette (French) is seeded to make that path visible in the demo.

## Design decisions worth knowing

**The LLM never picks the level.** Rules set a floor from can't-miss
presentations, evidence can only raise it, and low confidence rounds up. The
model extracts and explains. That is what makes this defensible in front of
judges, and it is enforced by `test_rules_are_never_overridden_downward`.

**Geriatric presentations are the point.** The rules encode the misses that
generic triage makes in older adults: an MI without chest pain (breathless plus
nausea or cold sweat), a UTI presenting as new confusion, a minor fall on an
anticoagulant, two abnormal vitals as a sepsis screen.

**No PHI over SMS.** iMessage and SMS are not HIPAA-grade, so an outbound text
carries a status word and a consented link. `notify.compose` asserts on leaks
and a test checks it.

**The model is on a leash.** LLM extraction is a *union* with the lexicon, so a
model can add what the lexicon missed but never delete a symptom that triggered
a red flag. Generated text is rejected unless it passes the style lint, stays in
the right language, and still contains the action word for its level. No key, a
timeout, bad JSON or elderspeak all land on the same deterministic template, and
`evaluation.llm_used` reports which path ran.

**Nothing here is real patient data.** Every senior and check-in is synthetic,
and every statistic is a labelled placeholder until the loaders run.

## Layout

```
backend/app
  schemas.py     the contract (see ../CONTRACT.md)
  rules.py       deterministic red flags -> level floor
  ladder.py      floor + evidence + confidence -> Evaluation
  persona.py     how we speak to an older adult (see ../docs/PERSONA.md)
  llm.py         OpenAI extraction + phrasing, guarded, always with a fallback
  retrieval.py   vector index: guidelines, cohort stats, their own history
  evidence.py    real cards when the warehouse is loaded, MOCK when it is not
  extraction.py  text -> symptoms, 6 languages, explicit negations
  explain.py     patient-facing wording per language
  notify.py      Linq send, PHI-free composition
  handoff.py     ED pre-arrival packet
  seed.py        four synthetic seniors, 14 days of history
  store.py       in-memory store + baseline computation
  events.py      /events fan-out
  datasets/      NHAMCS / NEISS / FAERS loaders -> DuckDB (see ../docs/DATA.md)
  routers/       api.py (REST), ws.py (WebSocket)
```

## Turning on the real integrations

Everything below is optional; the app runs without any of it.

```bash
# backend/.env  (gitignored -- never commit a key)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
DEEPGRAM_API_KEY=...
LINQ_API_KEY=...
MOCK_MODE=false        # only once Linq is real
```

Then `pytest -m live --live` to confirm the configured model still answers, and
`python scripts/smoke.py <deployed-url>` after every deploy.

## Next

1. Deepgram streaming audio into `POST /checkins` (`audio_url` returns 501 today;
   `GET /voice/agent-config/{id}` already returns the tuned Settings frame).
2. A real Linq send plus the inbound photo -> medication-list path.
3. Download and load the datasets (docs/DATA.md) so the MOCK badges disappear.
4. The NHAMCS under-triage model: triage-time features only, split by year,
   report AUC and calibration.
