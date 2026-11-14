# Demo runbook

Continuum is judged on **Statement Three: Finish the Job**. Every minute of the
demo answers one of the brief's three questions. This page is what to set up,
what to click and say, and what to do when something breaks.

## Before the demo

1. **Backend up, everything green.** `curl -s localhost:8000/api/health` must show
   `"database":"connected"`, `"vector_search":true`, `"embeddings":"voyageai:voyage-3.5"`,
   `"llm":true`, `"voice_configured":true`, `"ehr_ingestion":true`, `"tracing":true`.
2. **HAPI FHIR sandbox up** on `localhost:8080` with the three Synthea patients.
   If `scripts/ehr_sandbox/.manifest.json` is missing:
   `backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py manifest`.
3. **Patient 2 (Julius Jacobs) forgotten**, so the live discharge is a new patient:
   `backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py reset 2`.
   Patient 1 (Brent Hickle) stays ingested: that is what taught `order_memory`.
4. **Atlas open** in a second tab on these collections: `ingest_jobs`, `actions`,
   `order_memory`, `obligations`. Judges want to see the state in the database.
5. **LangSmith open** on project `continuum-hackathon`.
6. Console signed in as the coordinator (`coordinator@continuum.demo`).

## The three answers (about 5 minutes)

### 1. "How does it tell an action that took effect from one that was only sent?"

| Click | Say |
| --- | --- |
| **Call Room** → start a call for Michael Chen (`simulated` mode needs no phone or tunnel). Keep **Actions** open beside it. | "We record that we *asked* ElevenLabs to call. It shows as *intended*, then *sent*. It only becomes *confirmed* when the transcript comes back. That's the evidence." |
| Actions → **Attention** view | "If the process dies between the request and the reply, the action is *unknown*. We don't redial, because the call may have gone out. A human reconciles it. We never blindly retry." |
| Patient journey, Day 3 transcript (Demo runner → Day 3) | "Michael says he saw his doctor. We write it down, but a patient's word isn't proof. The follow-up stays open until the clinic confirms." |

Code: `backend/app/ledger.py`, `scheduler.py` (line ~443: interrupted call is not redialled), `calls.py`.

### 2. "How does it resume after a crash without replaying its whole history?"

Start the backend with the kill switch for this one:
`INGEST_CRASH_AFTER_STEP=structured DEMO_MODE=true .venv/bin/uvicorn app.main:app --port 8000`

| Click | Say |
| --- | --- |
| **Ingestion** page. Run `sandbox.py discharge 3` (Louie Armstrong). | "A discharge arrives from the hospital over HL7. The job runs pull → identify → structured → narrative → verify, and checkpoints each step in Atlas." |
| The job shows `structured ✓` and the backend dies. The page says the lease expired, "it likely crashed mid-job". Show `ingest_jobs` in Atlas. | "Each step's output and its 'done' mark are one write. The job never claims it finished work whose result isn't saved." |
| Restart the backend **without** the switch. | "On startup it picks up the job from the first unfinished step. Attempts says 2. The EHR record wasn't fetched again; the follow-ups weren't created twice." |

Code: `backend/app/ingest/jobs.py`, `main.py` (resume at startup), `streams.py`, `agent/checkpoint.py`.

### 3. "How does it adapt when a system's layout, fields or workflow changes underneath it?"

| Click | Say |
| --- | --- |
| Run `sandbox.py discharge 2 --recode`. It prints what changed in the EHR first. | "This hospital switched code sets. The cardiology referral has a new display, the lab has a local code and new wording, and there's an order we've never seen." |
| **Review** page for Julius Jacobs. | "The rules can't place any of these. Instead of dropping them or filing them as generic to-dos, it asks its own history." |
| Point at each order's method | "*Known code*: same SNOMED code seen before. *Similar order, 0.81*: Atlas Vector Search over past orders, with Voyage embeddings, matched 'Renal function panel' to 'Basic metabolic panel'. *Unrecognized*: 'Wound check' goes to a coordinator." |
| Confirm the Renal function panel. Show `order_memory` in Atlas. | "A coordinator's confirmation is learned. Next time that code arrives it's recognized exactly. Nothing was lost, and the uncertain ones went to a human." |

Code: `backend/app/ingest/orders.py` (known code → Vector Search → word overlap), `routes/review.py`.

### Close

"Verified task state, change streams, Vector Search and LangGraph checkpoints, all in Atlas.
Voice by ElevenLabs. Every agent run traced in LangSmith." Open the LangSmith tab.

## Numbers to quote

From `python -m evals.run` (21 synthetic cases; Continuum vs. the same extractor
with no memory and no loop engine):

- **100% vs 15%** of follow-ups in the right state after several conversations.
- **100% vs 2%** of open follow-ups still tracked after later calls.
- **0** patient self-reports treated as proof (the baseline closed 40% of them).
- **100% vs 0%** of date contradictions caught and preserved.

Why it matters to a clinic: Transitional Care Management billing (CPT 99495/99496,
roughly $200–300 per patient) requires documented contact within 2 business days
and a visit within 7 or 14 days. The action ledger is that documentation.

## Rehearsal loop

```
sandbox.py reset 2                  # forget Jacobs and what the recoded codes taught
sandbox.py discharge 2 --recode     # the adaptation demo
sandbox.py reset 2                  # clean again
```

## If something breaks

| Problem | Fallback |
| --- | --- |
| Atlas unreachable | `CONTINUUM_DB_MODE=memory`, restart. Everything runs in memory (no vector search; order matching uses word overlap). |
| Voyage down or rate-limited | `EMBEDDING_PROVIDER=local`, restart. The indexes rebuild themselves at 384 dims. "Renal function panel" will come up *unrecognized* instead of a vector match; the human-review path still demos. |
| LLM slow or down | Unset `LLM_API_KEY`. The deterministic extractor runs every day of the demo. |
| Tunnel or voice fails | Use `simulated` call mode. Day 3, 10 and 12 already show call transcripts. |
| A day runs out of order | Demo runner → Day 0 resets Michael, then replay. |
| Recode demo shows *known_code* for the lab | A previous run taught it. `sandbox.py reset 2` and run again. |
| HAPI patient already in Continuum | `sandbox.py reset N` before the discharge. |
