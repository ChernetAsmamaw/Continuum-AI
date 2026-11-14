# Performance

Where a discharge ingest, a result poll and the dashboard spend their time,
and what was changed to make them faster and cheaper without changing what
they produce.

## How it's measured

```bash
cd backend
.venv/bin/python -m evals.perf --label after          # writes evals/results/perf-after.json
.venv/bin/python -m evals.perf --compare before-1,before-2 after-1,after-2  # pooled markdown table
.venv/bin/python -m evals.perf --profile              # cProfile of one warm ingest
```

`evals/perf.py` runs in-process against **Atlas Local** (`continuum_perf`
database, dropped each run) and the **HAPI sandbox** loaded with Synthea
patients (`scripts/ehr_sandbox`). It never writes to the FHIR server. For
each operation it records wall time, **MongoDB round trips** (every command
the driver sends, `getMore` included, heartbeats excluded, attributed by
collection) and **FHIR requests** (with per-type request time). It measures:

* a full ADT^A03 → verified ingest of each sandbox patient on a fresh
  database (`--rounds` times), broken down by job step, plus a re-send of the
  same stay (the idempotent path);
* the result poller over 150 open EHR-backed follow-ups (the real ones plus
  synthetic follow-ups with distinct references, so each costs real lookups);
* `GET /api/dashboard`, `/api/patients`, `/api/patients/{id}/journey`,
  `/api/agent/approvals` through the FastAPI app (median of 5).

No LLM key (deterministic extraction); local embeddings
(BAAI/bge-small-en-v1.5 via fastembed).

## Results

Three before/after pairs were run back to back on the same data: a detached
checkout of the previous commit and this branch, each with the same benchmark
script, alternating, 2 fresh-database rounds over the 3 sandbox patients
each. That makes 18 ingests per side. The machine was shared with parallel
sessions (load average 14–19 on 8 cores), so wall times are noisy. **Round-trip
and request counts don't depend on load; trust those first.** Pooled
(`--compare before-1,before-2,before-3 after-1,after-2,after-3`):

| Operation | Before ms | After ms | Speed-up | Mongo round trips (before → after) | FHIR requests (before → after) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ingest (ADT^A03 → verified), median of 18 | 4 973 | 2 787 | 1.8× | 154 → 104 | 8 → 7 |
| · step pull | 1 426 | 446 | 3.2× | 0 → 0 | 8 → 7 |
| · step identify | 393 | 229 | 1.7× | 16 → 13 | — |
| · step structured | 984 | 275 | 3.6× | 39 → 10 | — |
| · step narrative | 1 734 | 1 304 | 1.3× | 60 → 57 | — |
| · step verify | 386 | 200 | 1.9× | 10 → 6 | — |
| Re-ingest same stay (idempotent) | 1 954 | 465 | 4.2× | 46 → 30 | 9 → 7 |
| Result poll, 150 open follow-ups | 9 406 | 1 520 | 6.2× | 83 → 79 | **372 → 15** |
| `GET /api/dashboard` | 103 | 56 | 1.8× | 15 → 7 | — |
| `GET /api/patients` | 41 | 23 | 1.8× | 9 → 3 | — |
| `GET /api/patients/{id}/journey` | 138 | 45 | 3.1× | 18 → 11 | — |
| `GET /api/agent/approvals` | 21 | 19 | 1.1× | 2 → 2 | — |

The slowest single ingest went from 34.1 s to 13.9 s. The first pair ran
while the machine was at its busiest, just before Atlas Local was stopped, and
shows the effect under contention most clearly: median ingest 27.5 s → 7.6 s,
poll 51.7 s → 7.2 s, dashboard 695 → 114 ms, journey 1 010 → 111 ms. The other
two pairs agree with each other (ingest 4.5 → 2.1 s and 4.0 → 1.8 s; poll
6.8 → 1.2 s and 9.4 → 1.5 s). Saved per-run results are in
`backend/evals/results/perf-{before,after}-{1,2,3}.json`.

The time saved per round trip scales with database latency. Atlas Local
answers in ~13 ms; a laptop talking to a cloud Atlas cluster often sees
30–80 ms, where cutting an ingest from 154 to 104 trips and the dashboard
from 15 to 7 saves proportionally more.

Response bodies are the same size before and after (`/api/patients` and the
journey byte-for-byte), and the ingestion eval (`python -m evals.ingestion`)
reports the same metrics as `docs/ingestion.md`.

## Where the time was going

**"A single ingest took ~30 s."** That was mostly a cold HAPI server. The
very first ingest measured took 104 s, 50 s of it in `pull` for only 9 FHIR
requests. A warm ingest, profiled before any change (8.1 s total), split as:

| Cost | Time | What |
| --- | ---: | --- |
| FHIR | ~3.4 s | 16 sequential requests at ~200 ms each; HAPI paging (`?_getpages=`) for the patient's whole DiagnosticReport history (275 reports, 3 pages) was the worst single item, up to 17 s under load |
| Embeddings | ~2.6 s | 15 single-text model calls per ingest (40–280 ms each); the patient summary was embedded twice per agent run |
| MongoDB | ~2.3 s | 165 round trips at ~13 ms each on Atlas Local; per-obligation `find_one` + 6–7 writes, a full job re-read after every step |
| Agent graph | rest | LangGraph execution and checkpoint writes (19 per narrative step) |

The poller made 3 `based-on` searches per order reference, one at a time:
360 requests for 150 follow-ups. The read endpoints were N+1: one
obligations query per patient (`/api/patients`, and again in the dashboard,
which also read every obligation a second time and the approval queue twice),
and one evidence query per obligation (journey).

## What changed

**FHIR pull** (`ingest/pull.py`)
* Once the encounter is known, the five per-stay searches (discharge summary
  + attachment, DiagnosticReport, ServiceRequest, Appointment,
  MedicationRequest) run concurrently. The encounter lookup by visit number
  runs while the patient is looked up. Results are processed in the original
  order, so the packet is identical.
* `status=` is sent server-side for DiagnosticReport (registered, partial,
  preliminary) and ServiceRequest (active, on-hold, draft). This removed HAPI
  paging over the whole report history. The client-side filters stay, so a
  server that ignores the parameter gives the same packet. If a server
  *rejects* it, the search is retried without it (`search_narrowed`).
  Side benefit: the old `_count=100 × 5 pages` cap could miss a pending report
  for a patient with more than 500 reports; now it can't.
* `FHIRClient` serialises SMART token refreshes with a lock, since searches
  now share the client across threads.

**Result poller** (`ingest/poller.py`)
* References are de-duplicated across follow-ups. `based-on` searches carry
  up to 25 order references each (comma = OR). Direct references
  (DiagnosticReport/Appointment/Encounter) are fetched with one `_id` search
  per type and batch. All requests run concurrently (8 workers).
* Each resource from a batched search is attributed only to the references
  it actually cites in `basedOn`, so exact matching holds even on a server
  that ignores the parameter. Results are applied in the same order as the
  one-at-a-time lookup. A server that refuses list values is queried one
  reference at a time. Failed direct reads are still reported per reference.

**Structured step** (`ingest/builder.py`, `repository.py`, `ingest/orders.py`)
* One "already tracked?" query for all plans instead of one per plan. Refs
  claimed earlier in the same run are skipped, as before.
* `repository.create_obligations_bulk` writes the obligations, evidence,
  audit entries and memories with `insert_many`: 4 round trips instead of
  ~7 per follow-up, plus one batched embedding call. It builds exactly the
  documents `create_obligation` + `add_evidence` build; both now share the
  same document builders. Ids are deterministic (patient + EHR reference), and
  the obligation, which is the "done" marker, is written last. A step re-run
  after a crash between those writes completes the missing documents without
  duplicating any. Before, a crash between an obligation and its evidence
  left the follow-up without provenance for good.
* Order memory is learned with one lookup, one embedding call and one insert.
  The classifier checks the vector index once per ingest, not once per
  unrecognized order.
* `verify` reads back all planned references in one query.
* Identity matching uses one query for all identifiers (first identifier in
  EHR order still wins). The name scan projects only id, name and birth date.

**Jobs** (`ingest/jobs.py`): after each checkpoint the job document (packet
included) is no longer re-read. The same `$set` is applied to the in-memory
copy; the worker holds the lease, so they're identical. Saves 6 round trips
and the packet transfer per ingest.

**Embeddings** (`agent/llm.py`): `embed_texts()` embeds many texts in one
model call (`query_embed` on a list for the local model, the same vectors as
`embed_query`), and an LRU of 1 024 vectors per process serves repeated texts.
The cache is cleared when the model is re-picked.

**Agent memory** (`agent/memory.py`): building the retrieval context reads
the patient and their obligations once and passes them to the summary
refresh, instead of reading the patient three times and the obligations
twice.

**Read endpoints** (`repository.py`): `list_patients` makes 2 queries (was
1 + one per patient). `dashboard` reads every obligation once and shares it
between patient rows and stats, and reads pending approvals once.
`journey` gets all evidence in one `$in` query, ordered as the per-obligation
index scan (newest first, ties in insertion order, which matters because
timestamps have one-second precision).

**Indexes** (`db.py`), each checked with `explain()`:

| Query | Before | After |
| --- | --- | --- |
| dashboard recent events (`sort occurred_at`) | COLLSCAN + SORT | IXSCAN `occurred_at_-1` |
| dashboard recent audit (`sort created_at`) | COLLSCAN + SORT | IXSCAN `created_at_-1` |
| approval queue (`status`, `sort created_at`) | COLLSCAN + SORT | IXSCAN `status_1_created_at_-1` |
| `ingest_jobs` by `id` (every step) / by `key` / by `status` | COLLSCAN | IXSCAN (`id` unique) |
| `ingest_runs` by `job_id` (verify) | COLLSCAN | IXSCAN |
| `order_memory` by `key` / `code` | COLLSCAN | IXSCAN |
| poller: open follow-ups with `fhir_refs` | COLLSCAN | IXSCAN on a partial index (EHR follow-ups only) |
| `actions` ledger by `id`, `key`, `external_ref`, status sweeps | `_id` only | indexed |

## Guarantees kept

Covered by `tests/test_performance.py` (15 tests) on top of the existing
suite (93 + 12 Atlas live tests, all passing):

* pull packets are identical against servers that honour, ignore or reject
  the `status` filter;
* batched poll lookups equal one-at-a-time lookups, including the
  rejected-list fallback and a server that ignores `based-on`;
* bulk creation writes the same obligations, evidence, audit and memories as
  one-at-a-time creation, and a partial write followed by a re-run completes
  without duplicates;
* a re-run of the structured step creates nothing;
* `list_patients`, `dashboard` and `journey` equal naive reference
  implementations;
* the job document returned in memory equals the stored one;
* order memory is learned once per key;
* batched embeddings equal single embeddings and are cached.

## Still on the table

* **Narrative step** (now the largest share): 19 of its 56 round trips are
  LangGraph checkpoint writes, and most of the rest is in `agent/graph.py`,
  `engine.py` and `workflow.py` (outside this pass). Batching the
  checkpointer's `put_writes` and having `intake_patient` use
  `create_obligations_bulk` would be the next wins.
* `integrations._ingest_one` (used by the poller per new result) makes ~10
  round trips per resource, mostly patient and approval lookups.
* Cold HAPI dominates a first ingest. A real EHR won't behave like a
  just-started HAPI, but concurrency now overlaps those cold searches instead
  of adding them up.
