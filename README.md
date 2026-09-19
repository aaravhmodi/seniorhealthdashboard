# Senior check-in backend

Voice or text check-in from an older adult, in their language, turned into a
structured record, run through a deterministic safety layer, placed on a
four-rung action ladder, pushed to the family over Linq, and handed to the ED as
a pre-arrival packet when it matters.

**Sprint 0 is done and running.** The contract is frozen, the server is live, the
demo path works end to end, and 26 tests guard it.

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

| piece | Sprint 0 | lands in |
|---|---|---|
| symptom extraction | multilingual lexicon, deterministic | Sprint 1 (LLM, same signature) |
| red-flag rules | **real** | — |
| action ladder | **real** | — |
| NEISS / FAERS numbers | placeholders, `source` starts with `MOCK` | Sprint 2 |
| baseline (own history) | **real**, split-half trend | refined in Sprint 2 |
| explanations | templates, 5 languages | Sprint 1 (LLM phrasing) |
| Linq send | mocked unless `LINQ_API_KEY` set and `MOCK_MODE=false` | Sprint 1 |
| Deepgram | `audio_url` returns 501 | Sprint 1 |
| storage | in memory, behind a `Store` interface | Sprint 2 |

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

**Nothing here is real patient data.** Every senior, check-in and statistic is
synthetic.

## Layout

```
backend/app
  schemas.py     the contract (see ../CONTRACT.md)
  rules.py       deterministic red flags -> level floor
  ladder.py      floor + evidence + confidence -> Evaluation
  evidence.py    NEISS / FAERS / baseline cards  (MOCK numbers)
  extraction.py  text -> symptoms, 5 languages, explicit negations
  explain.py     patient-facing wording per language
  notify.py      Linq send, PHI-free composition
  handoff.py     ED pre-arrival packet
  seed.py        three synthetic seniors, 14 days of history
  store.py       in-memory store + baseline computation
  events.py      /events fan-out
  routers/       api.py (REST), ws.py (WebSocket)
```

## Next

Sprint 1: Deepgram streaming into `POST /checkins`, the LLM extractor behind
`extraction.extract`, a real Linq send, and the NEISS/FAERS loaders into
Parquet/DuckDB.
