# API contract (v0.1.0)

**This is the team interface.** The UI builds against it, the data track fills it
in. Additive changes are fine. Renames are a team-wide announcement.

Live reference (always more current than this file):

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Contract summary: `http://localhost:8000/contract`

## The action ladder

Every evaluation lands on exactly one rung.

| level | meaning | who gets told |
|---|---|---|
| 1 | Log and monitor | nobody |
| 2 | Call your clinic or pharmacist today | caregivers who consented |
| 3 | Go to the emergency department | caregivers + ED handoff packet built |
| 4 | Call 911 now | caregivers + ED handoff packet built |

Rules set a floor, evidence can only raise it, and low confidence escalates one
rung. Nothing lowers a rule's floor.

## Endpoints

| method | path | returns | notes |
|---|---|---|---|
| POST | `/checkins` | `{checkin, evaluation, notifications}` | the whole demo runs through this |
| GET | `/checkins/{id}` | same shape | |
| GET | `/seniors` | `Senior[]` | |
| GET | `/seniors/{id}` | `Senior` | |
| GET | `/seniors/{id}/checkins?limit=` | `CheckIn[]` | newest first |
| GET | `/seniors/{id}/timeline?window_days=` | `{entries[], baseline}` | newest first |
| GET | `/seniors/{id}/baseline?window_days=` | `BaselineSummary` | |
| GET | `/seniors/{id}/latest-evaluation` | `Evaluation` | |
| GET | `/handoff/{senior_id}?force=` | `HandoffPacket` | 404 below level 3 unless `force=true` |
| GET | `/evidence/preview?senior_id=&text=` | `EvidenceCard[]` | no check-in stored |
| GET | `/events/recent?limit=` | `WSEvent[]` | polling fallback for the socket |
| GET | `/voice/agent-config/{senior_id}` | Deepgram Settings frame | senior-tuned endpointing, retrieved context inlined |
| GET | `/voice/coverage` | what we can hear vs speak | `text_only` drives the text fallback |
| POST | `/voice/transcribe` | `{text, confidence, ...}` | multipart audio; 503 without a key, 502 on failure |
| POST | `/voice/speak` | `audio/wav` | 409 where the language has no voice |
| GET | `/retrieval/search?q=&senior_id=&k=` | scored chunks | inspect what the model is given |
| POST | `/retrieval/reindex` | `{indexed_chunks}` | rebuild the vector index |
| POST | `/questions/answer` | grounded answer + citations | optional `senior_id` scopes private history |
| GET | `/datasets/status` | warehouse + table availability | which cards are real vs MOCK |
| POST | `/webhooks/linq` | `{ok, senior_id, ...}` | backend only, signature-checked off mock |
| POST | `/demo/reset` | `Health` | re-seeds; safe to call between demo runs |
| GET | `/demo/scenarios` | scripted demo inputs + expected level | |
| WS | `/events` | `ready` frame, then one frame per event | `ping` every 20s |

### POST /checkins

```jsonc
// request
{
  "senior_id": "sen_rosa",
  "source": "voice",            // voice | text | caregiver | scheduled
  "language": "es",
  "text": "Me falta el aire y tengo nausea, y estoy sudando frio.",
  "transcript_confidence": 0.93, // 0..1, from Deepgram
  "vitals": { "systolic": 128, "heart_rate": 88, "spo2": 96 },
  "client_ref": "ui-123"         // echoed back, for optimistic rendering
}
```

```jsonc
// response (abridged)
{
  "checkin": { "id": "chk_00021", "symptoms": [{ "label": "shortness of breath" }] },
  "evaluation": {
    "level": 3,
    "level_label": "Go to the emergency department",
    "red_flags": [{ "code": "cardiac_acs", "forces_level": 3, "matched_on": ["..."] }],
    "evidence": [{ "kind": "neiss", "title": "...", "stat": { "value": 48.2 }, "source": "MOCK ..." }],
    "explanation": "Lo que me describio debe revisarse hoy en un hospital...",
    "explanation_en": "What you described should be looked at in a hospital today...",
    "recommended_actions": ["Go to the emergency department now.", "..."],
    "confidence": 0.77,
    "escalated_for_uncertainty": false,
    "requires_human_review": true,
    "llm_used": true,
    "llm_fallback_reason": null,
    "context_citations": ["[guideline: ...]", "[patient_history: check-in chk_00012]"]
  },
  "notifications": [{ "to": "cg_priya", "body": "Rosa needs to be seen ...", "status": "mocked" }]
}
```

`audio_url` is transcribed with Deepgram nova-3 and then runs the normal
ladder; the response carries the transcript as `checkin.raw_text` and the
confidence as `transcript_confidence`. It fails **loudly** on purpose — 503 if
no key is configured, 502 if transcription fails — because a dropped
transcription must never look like a patient who reported nothing.

### WebSocket /events

First frame on connect:

```json
{ "type": "ready", "backlog": [ /* last 25 events */ ] }
```

Then one frame per event, and `{"type":"ping"}` on an idle 20s:

```json
{ "type": "evaluation.completed", "at": "...", "senior_id": "sen_rosa",
  "payload": { "evaluation_id": "eval_00022", "level": 3 } }
```

Event types: `checkin.created`, `evaluation.completed`, `level.changed`,
`meds.updated`, `handoff.ready`, `caregiver.message`.

## UI rendering rules

1. **Mark the mock.** Any `EvidenceCard.source` starting with `MOCK` is a
   placeholder. Render a badge. Nobody puts those numbers in the deck.
2. **`requires_human_review: true`** means a clinician confirms before the
   patient-facing screen presents it as settled.
3. **`explanation`** is for the senior, in their language. **`explanation_en`**
   is the clinician string and is always English.
4. **`escalated_for_uncertainty: true`** means we did not hear enough and rounded
   up. Say so in the UI; it is a trust feature, not a bug.
5. **`llm_used: false` with a `llm_fallback_reason`** means the deterministic
   template was used. That is a normal, safe state, not an error to surface.
6. **`_meta.voice_output: false`** from the voice config means Deepgram has no
   voice for that language. Render text and show the "you can also type" line.
7. Caregiver texts carry a status word and a link. Never render PHI as if it
   had been sent by SMS.

## Seeded demo data

`sen_rosa` (79, Spanish, 4 meds, 14 days of history with dizziness and poor
appetite trending up), `sen_chen` (84, Chinese, warfarin), `sen_walter` (76,
English, steady control case), `sen_henriette` (82, French, text-fallback
because Deepgram has no French voice). All synthetic. `POST /demo/reset` restores them.
