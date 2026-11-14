# EHR ingestion

Every discharge reaches Continuum without anyone copy-pasting. A hospital
discharge message (HL7 `ADT^A03`) triggers a FHIR pull of the stay. Follow-ups
are built from structured orders first, and the discharge narrative fills in
only what the orders missed. Results that come back later are matched to
their order by id and queue a one-click, evidence-backed closure.

```text
ADT^A03 ──▶ POST /api/ingest/hl7 (or MLLP) ──▶ FHIR pull ──▶ DischargePacket ──▶ follow-ups
                                                                              (orders → structured,
EHR results ◀── poller (scheduler sweep / POST /api/ingest/poll)               narrative → AI, deduped)
     └──▶ exact basedOn match ──▶ clinic evidence + verify_completion approval
```

## What becomes a follow-up

| EHR record (FHIR R4) | Follow-up | Origin |
| --- | --- | --- |
| `ServiceRequest` referral (active, this stay) | "{Specialty} follow-up", due from the order | `ehr_structured` |
| `ServiceRequest` lab / imaging | The test, owner Laboratory / Imaging | `ehr_structured` |
| `Appointment` booked against a referral | Same follow-up, already **scheduled** with the clinic date | `ehr_structured` |
| `DiagnosticReport` registered/partial/preliminary | "Review pending result: {test}", owner the attending | `ehr_structured` |
| New `MedicationRequest`(s) | One medication review | `ehr_structured` |
| Discharge summary (`DocumentReference`, LOINC 18842-5) | Anything the orders didn't cover (e.g. home care) | `ai_extracted` |

Every structured follow-up stores `fhir_refs` and evidence pointing at the
exact resource version. National identifiers (SSN) are never stored or used
for matching.

**Identity:** exact EHR id or MRN reuses the patient. A unique name plus
birth-date match also reuses. Same-name candidates without a matching
identifier are **never merged**: a new record is created and flagged
`identity_review`.

**Idempotency:** the same HL7 control id is acknowledged as a duplicate. A new
message or pull for the same encounter only adds orders that are new since the
last pull. Results are deduplicated per resource version.

## Finishing the job: crashes, proof, and changing systems

**Resumable jobs.** Each discharge runs as a job in `ingest_jobs` with steps
`pull → identify → structured → narrative → verify`. A step's output is saved
in the same update that marks it done (the pulled EHR record is stored after
`pull`), so a resumed job continues at the first unfinished step and never
re-fetches or re-creates finished work. Jobs are claimed with an atomic lease.
A restarted process reclaims a lease whose owner process on the same machine
is gone, so it resumes immediately. `verify` reads back every planned
follow-up and fails the job if a write didn't land. Jobs resume at startup, in
every scheduler sweep, when the same HL7 message is re-sent, or via
`POST /api/ingest/jobs/{id}/resume`. For a live demo, start the API with
`INGEST_CRASH_AFTER_STEP=structured` (honoured only with `DEMO_MODE=true`):
the process exits right after that checkpoint, and a normal restart finishes
the job.

**Action ledger (`actions`, `GET /api/actions`).** Outbound calls and
coordinator tasks move `intended → sent → confirmed`. Only the expected proof
confirms an action: the post-call transcript for that conversation, or the
coordinator's decision on the task. The sweep flags `unconfirmed` (sent, no
proof by the deadline) and `unknown` (interrupted between deciding and hearing
back). An interrupted call is never redialled automatically.

**Adapting to EHR changes (`order_memory`).** Every order the rules classify
is remembered with an embedding. When an order arrives that the rules don't
recognize (new local codes, missing category), Continuum looks it up in three
ways:

1. The same code seen before.
2. Atlas Vector Search over past orders, with a score of at least 0.72.
3. Word overlap, when no embeddings are available.

The order becomes the right kind of follow-up, flagged for a coordinator to
confirm. If nothing matches, it is still created, marked "unrecognized",
never dropped.

**Database-enforced state.** `$jsonSchema` validators on `obligations`,
`actions`, `ingest_jobs` and `evidence` reject malformed status values.

**Live updates.** A change-stream watcher follows the task-state collections
and streams them to the console over SSE (`GET /api/stream`). It saves its
resume token, so a restart continues where it left off.

Tests: `tests/test_resilience.py` covers these on the mock database.
`tests/test_atlas_live.py` covers validators and change streams on a real
replica set: set `ATLAS_TEST_URI`, e.g. Atlas Local.

## API

All endpoints take the `X-Continuum-Integration-Token` header (or a
signed-in coordinator).

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/ingest/hl7` | HL7v2 message (ER7 text, or JSON `{"message": "..."}`); returns the outcome plus an ACK (`AA`/`AE`/`AR`) |
| `POST` | `/api/ingest/pull` | `{patient_fhir_id | mrn, encounter_fhir_id?}`: pull on demand / backfill |
| `POST` | `/api/ingest/poll` | Check the EHR now for results behind open follow-ups |
| `GET`  | `/api/ingest/status` | Config, recent ingests, HL7 messages, last poll |
| `GET`  | `/api/ingest/jobs`, `/api/ingest/jobs/{id}` | Jobs with per-step checkpoint state |
| `POST` | `/api/ingest/jobs/{id}/resume` | Continue a job from its first unfinished step |
| `GET`  | `/api/actions` | Action ledger (`?patient_id=`, `?status=unconfirmed,unknown`) |
| `GET`  | `/api/stream?token=` | Change-stream events (SSE) |

The scheduler sweep also polls when `EHR_FHIR_BASE_URL` is set and
`EHR_POLL_ENABLED=true`. Set `HL7_MLLP_PORT` to accept MLLP from an interface
engine. It binds `127.0.0.1` and has no auth of its own, so tunnel it.

## Configuration

| Variable | Meaning |
| --- | --- |
| `EHR_FHIR_BASE_URL` | FHIR R4 base, e.g. `http://localhost:8080/fhir` |
| `EHR_AUTH_MODE` | `none`, `bearer` (`EHR_BEARER_TOKEN`), or `backend_jwt` |
| `EHR_CLIENT_ID`, `EHR_TOKEN_URL`, `EHR_PRIVATE_KEY_PATH`, `EHR_KEY_ID` | SMART Backend Services: RS384-signed client assertion, `client_credentials` grant (Epic "Backend Systems" apps) |
| `EHR_SCOPES` | Defaults to `system/*.read` for the resources above |
| `EHR_POLL_ENABLED`, `HL7_MLLP_PORT`, `HL7_MLLP_HOST` | Poller and MLLP listener |

## Local sandbox (HAPI FHIR + Synthea)

Needs Docker (Docker Desktop or colima) and Java 17 for Synthea.

```bash
# 1. FHIR server
docker compose --profile ehr up -d hapi        # http://localhost:8080/fhir

# 2. Synthetic patients (Synthea; ~60 people aged 65-90, 3 years of history)
curl -LO https://github.com/synthetichealth/synthea/releases/download/master-branch-latest/synthea-with-dependencies.jar
java -jar synthea-with-dependencies.jar -s 7 -cs 7 -p 60 -a 65-90 \
  --exporter.fhir.transaction_bundle=true --exporter.years_of_history=3 \
  --exporter.baseDirectory=./synthea-output Massachusetts

# 3. Load living heart-failure patients + a fresh discharge scenario each
backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py load --synthea ./synthea-output/fhir

# 4. In .env: EHR_FHIR_BASE_URL=http://localhost:8080/fhir and an INTEGRATION_SECRET; start the API
# 5. Discharge, record results, close loops
backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py discharge 1
backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py results 1
backend/.venv/bin/python scripts/ehr_sandbox/sandbox.py poll
```

Synthea has real-looking histories but no discharge orders, so the loader adds
a heart-failure admission to each patient (`app/ingest/scenarios.py`). The
admission includes a discharge summary, cardiology referral, BMP order,
primary-care referral (booked, in one variant), a test still pending at
discharge, and new diuretics.

## Evaluation

`python -m evals.ingestion` (from `backend/`) compares EHR ingestion with the
current "paste the discharge summary" intake on 8 synthetic discharges:

| Metric | EHR ingestion | Paste summary |
| --- | --- | --- |
| Expected follow-ups tracked | 100% | 67% |
| Tests pending at discharge tracked | 100% | 0% |
| Duplicate follow-ups | 0% | 0% |
| Follow-ups backed by a structured EHR order | 83% | 0% |
| Results matched to the right follow-up | 100% | 58% |
| Manual steps per discharge | 0 | 1 |

These scenarios were written for this project, so they show the design works
end to end, not real-world accuracy. The deterministic extractor was used (no
LLM key).

## Not done yet

- Real hospital connectivity: VPN/interface engine, production Epic/Oracle
  app approval. The JWT client is unit-tested but not yet run against
  fhir.epic.com, which needs a registered developer app.
- FHIR Subscriptions or vendor event feeds instead of polling.
- PDF/fax document intake.
- A UI for the identity-review queue (flag and event exist; there is no screen).
