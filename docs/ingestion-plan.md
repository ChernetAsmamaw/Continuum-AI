# Ingestion layer — build plan

Status: phases 0–3 implemented 2026-11-13; see [ingestion.md](ingestion.md). Phase 4 (Epic sandbox) needs a registered app; phase 5 (documents) not started.

## 1. Why

Every rule in Continuum (proof before closure, preserved conflicts, chasing
overdue loops) only protects follow-ups that exist in the system. Today a
follow-up exists only if a coordinator types or pastes a discharge summary.
The hospital side is manual, so loops can be missed before tracking starts.

**Goal:** every discharge enters Continuum automatically, its follow-ups come
from structured EHR data first (AI extraction as the fallback), and results
that come back later close loops without anyone retyping them.

### Success criteria (demo-level)

1. A simulated discharge message creates the patient, episode and follow-ups
   with **no human input**, in under 10 s.
2. At least 80% of follow-ups in the test set come from structured
   resources (orders, referrals, appointments), not free-text extraction.
3. Tests pending at discharge become follow-ups, and they close (via an
   evidence-backed approval) when the result is final.
4. Replaying the same message or bundle twice changes nothing (idempotent).
5. Every follow-up records where it came from (`source_ref` back to
   the HL7 message or FHIR resource version).

## 2. What already exists (reuse, don't rebuild)

| Piece | Where | Reuse as |
| --- | --- | --- |
| Patient + episode + follow-up creation | `workflow.intake_patient` | The write path for new discharges |
| Free-text extraction (LLM + rules) | `agent/extraction.py`, graph `extract` node | Fallback for the discharge summary narrative |
| FHIR **push** intake (Appointment, Encounter, DiagnosticReport, Observation) | `integrations.py`, `POST /api/integrations/fhir` | The closure path: results/visits → evidence → approval |
| Follow-up matching | `engine.rank_obligations` | Matching results back to follow-ups (to be strengthened with order IDs, §5.4) |
| Idempotency on `resourceType/id/versionId` | `integrations._ingest_one` | Same key scheme for all new adapters |
| Scheduler sweep | `scheduler.run_sweep` | Host for the new result poller |

Gap summary: there is no **trigger** (knowing a discharge happened), no
**pull** (fetching the record), and no **structured order → follow-up
mapping**. Closure matching is fuzzy text only.

## 3. Target architecture

```text
 Sources                     Adapters                 Core (new)                 Existing
 ─────────                   ────────                 ──────────                 ────────
 HL7v2 ADT A03 ──────────▶  adt_listener  ──┐
 (MLLP or HTTP)                              │
                                             ├──▶  DischargePacket  ──▶  obligation_builder ──▶ intake_patient
 FHIR server  ◀── pull ───  fhir_client  ───┘     (normalized)          (structured first,      (+ graph extract
 (Epic / Oracle / mock)                                                   LLM fallback, dedupe)   for narrative)
                                                                                │
 PDF / fax upload ───────▶  doc_adapter  ──────────────────────────────────────┘

 FHIR server  ◀── poll ───  result_poller (in scheduler) ──▶ existing ingest_fhir ──▶ evidence + approval
 Clinic push ────────────────────────────────────────────▶ existing POST /api/integrations/fhir
```

Design rules:

- **One internal format.** Every adapter emits a `DischargePacket`; nothing
  downstream knows whether data came from HL7, FHIR, or a PDF.
- **Structured first, AI second.** Orders, referrals and booked appointments
  become follow-ups directly with high confidence. The discharge narrative
  still goes through extraction, but its output is deduped against the
  structured items and is labelled lower-confidence.
- **Pull, don't wait.** Don't assume clinics will push. The poller re-queries
  the EHR for the orders behind open follow-ups.
- **Provenance everywhere.** Each follow-up and evidence row carries the
  source message/resource version.
- **Same product rules.** New data enters through the existing engine, so
  "a patient report is not proof" and the approval gates are unchanged.

## 4. Test environment (no hospital access needed)

| Need | Choice | Notes |
| --- | --- | --- |
| Realistic patient records | **Synthea** (Java; Java 11 is already installed on this machine) | Outputs one FHIR R4 Bundle per patient. Confirm whether a heart-failure module exists; if not, hand-author 2–3 CHF bundles on top of Synthea output. |
| A FHIR server to pull from | **Built-in mock EHR** (`backend/mock_ehr/`, small FastAPI app serving Synthea bundles with search on `patient`, `_id`, `type`, `status`) | Docker isn't installed, so a HAPI container is out for now. The mock also gives a deterministic demo with no network risk. |
| A real vendor API shape | **Oracle Health open sandbox** (read-only, no auth) | Use for a read-only "it works against a real vendor" check. Reported 502s → never on the demo critical path. |
| Real OAuth flow | **Epic sandbox** at fhir.epic.com, Backend Systems app, JWT client credentials (RS384) | Needs a developer account and app registration **by the user**; registration may take up to a day. Optional, phase 4. |
| Discharge trigger | **HL7v2 ADT^A03** samples generated from the same Synthea patients | Parse with `python-hl7` (simple) or `hl7apy` (has an MLLP server). |

## 5. Components

### 5.1 `DischargePacket` (new, `app/ingest/packet.py`)

Pydantic model, the only contract between adapters and core:

```text
DischargePacket
  source: "hl7v2" | "fhir" | "document"
  source_ref: str                      # message control id or Encounter/123/_history/2
  patient: identifiers[], name, birth_date, phone, language
  encounter: id, admit_date, discharge_date, reason, discharge_disposition
  discharge_summary_text: str | None   # DocumentReference LOINC 18842-5, decoded
  orders: [ {kind: lab|imaging|referral|other, code, display, status, ordered_at, fhir_ref} ]
  appointments: [ {start, service, practitioner, status, fhir_ref} ]
  medications_new_or_changed: [ {display, change, fhir_ref} ]
  pending_results: [ {order_ref, display} ]   # ordered but not final at discharge
```

### 5.2 Adapters (`app/ingest/adapters/`)

1. **`adt.py`: HL7v2 discharge trigger**
   - `POST /api/ingest/hl7` accepts a raw HL7 message (easy to test with curl).
     Optional MLLP listener (`hl7apy`) behind a setting, since real interface
     engines speak MLLP.
   - On `ADT^A03`: read PID (identifiers, name, DOB, phone), PV1 (visit,
     discharge date/disposition), then **trigger a FHIR pull** for that
     patient/encounter. ADT alone is just the doorbell.
   - Ack with an `MSA|AA` message; idempotent on MSH-10 (message control ID).
2. **`fhir_pull.py`: record fetch**
   - Pluggable client with three auth modes: `none` (mock, Oracle open),
     `backend_jwt` (Epic: signed RS384 JWT → token endpoint, system scopes),
     `bearer` (manual token for experiments).
   - On trigger, fetch: `Encounter`, `Patient`, `DocumentReference?type=18842-5`
     (discharge summary), `ServiceRequest?patient=&status=active`,
     `Appointment?patient=&date=ge<discharge>`, `MedicationRequest?patient=`
     (new/changed since admit), plus lab `DiagnosticReport`s that aren't final.
   - Also usable without ADT: `POST /api/ingest/fhir-pull {patient_id}` for
     manual or backfill runs.
3. **`document.py`: PDF/fax** (phase 3)
   - Upload endpoint; text-layer extraction first (`pypdf`), OCR only if
     there's no text layer. Output: a packet with just `discharge_summary_text`
     plus whatever patient identifiers can be parsed. Goes to coordinator
     review before patient creation, since identity is uncertain.

### 5.3 Follow-up builder (`app/ingest/builder.py`)

Mapping rules (structured → follow-up):

| Input | Follow-up | Confidence |
| --- | --- | --- |
| `ServiceRequest` referral (category = referral / `performerType` set) | "See {specialty} by {occurrence or default window}" | 0.95 |
| `ServiceRequest` lab/imaging, active at discharge | "{test} completed" | 0.95 |
| Ordered test with no final result at discharge | "Review pending result: {test}", owner = discharging clinician | 0.95 |
| Future `Appointment` booked | Follow-up created as **scheduled** with clinic-confirmed date | 0.95 |
| New/changed `MedicationRequest` | "Medication review with PCP" (one per episode, lists meds) | 0.8 |
| Discharge summary text | Existing graph extraction; result deduped against the above | existing |

Dedupe: before creating an AI-extracted follow-up, run `rank_obligations`
against the structured ones; above a threshold, attach the text as supporting
evidence instead of creating a duplicate.

### 5.4 Stronger closure matching

Store `fhir_refs` (ServiceRequest IDs) on each structured follow-up. In
`integrations._ingest_one`, check `DiagnosticReport.basedOn` /
`Appointment.basedOn` → exact match **before** falling back to fuzzy text
ranking. Exact-ID matches can carry higher confidence. Closure still needs
approval; that rule stays.

### 5.5 Result poller (`scheduler.py` addition)

Each sweep: for open follow-ups with `fhir_refs`, query the source FHIR
server for linked `DiagnosticReport`/`Appointment`/`Encounter` updates since
the last check, and feed anything new to the existing `ingest_fhir`.
Rate-limited per source; cursor stored per patient.

### 5.6 Patient identity

Match on (system, value) identifiers first (MRN), then on name + DOB. Exact
match → reuse. No match → create. Multiple or partial matches → **do not
merge**; queue a coordinator review approval (new approval kind
`identity_review`). Wrong-patient merges are the worst failure in this layer.

## 6. Phases

| Phase | Scope | Done when |
| --- | --- | --- |
| **0. Test data** (~0.5 day) | Run Synthea (FHIR R4) for ~20 patients; pick/author 3 heart-failure discharges; write ADT^A03 generator from those bundles; commit fixtures under `backend/tests/fixtures/ehr/` | Fixtures load; each has Encounter, discharge note, ≥2 orders, ≥1 pending lab |
| **1. Mock EHR + FHIR pull + builder** (~2 days) | `mock_ehr` app; `DischargePacket`; `fhir_pull` (auth `none`); builder + dedupe; `POST /api/ingest/fhir-pull` | Pulling a fixture patient creates correct follow-ups with `source_ref`s; replay is a no-op; tests green |
| **2. ADT trigger** (~1 day) | `POST /api/ingest/hl7`, A03 parse → pull; ACK; idempotency on MSH-10; demo button "simulate discharge" | One curl of an A03 → patient + follow-ups appear in console |
| **3. Closure loop** (~1.5 days) | `fhir_refs` + exact `basedOn` matching; result poller; mock EHR endpoint to "finalize" a lab | Finalizing a lab in the mock → evidence + one-click approval in console, no human data entry |
| **4. Real vendor checks** (~1–2 days, partly blocked on registration) | Oracle open sandbox read check; Epic Backend Systems JWT client against fhir.epic.com | Same pull code reads a real vendor sandbox patient; mapping gaps logged |
| **5. Documents** (optional, ~1 day) | PDF upload, text extraction, identity review | Uploaded PDF yields a reviewed patient + extracted follow-ups |

Rough total: about 6–8 focused days for phases 0–4.

### Evaluation hook

Extend `backend/evals` with an ingestion suite: for each fixture, expected
follow-ups vs. created ones (precision/recall), % structured vs. AI-extracted,
duplicate rate, and pending-result closure rate.

## 7. Risks and open questions

- **Synthea realism.** Synthea records are coherent but its discharge notes
  are thin, and there may be no specific heart-failure module. Mitigation:
  hand-author narrative discharge notes for the demo patients.
- **Order-to-result linkage is often missing in real data.** `basedOn` isn't
  always populated, so keep fuzzy matching as a fallback and measure how
  often each path fires.
- **Vendor sandboxes are flaky or limited.** Oracle's open endpoint has
  reported 502s; Epic sandbox IDs are encrypted and its patients are fixed.
  The mock EHR stays the demo's critical path.
- **Polling vs. subscriptions.** Production would prefer FHIR Subscriptions
  or vendor event feeds; support varies by vendor. Polling is the simple,
  portable start.
- **PHI.** Real-sandbox data is synthetic, but the pull client will store
  tokens and keys: keep them in env/secret storage, never in fixtures. See
  `docs/compliance.md`.
- **Decision needed:** should ADT-created patients start tracking
  immediately, or wait in a "new discharge" review queue for a coordinator
  to confirm? Recommendation: track immediately, flag low-confidence items.

## 8. Out of scope (for now)

Real hospital interface-engine connectivity (VPN, Mirth/Rhapsody), an
embedded SMART on FHIR app inside Epic, bulk `$export`, write-back to
the EHR, SMS channel.

## References

- Epic sandbox / backend services: <https://fhir.epic.com>, guide: <https://nirmitee.io/blog/epic-fhir-integration-guide/>
- Oracle Health open sandbox: <https://docs.oracle.com/en/industries/health/millennium-platform-apis/build-smart-on-fhir-apps>, <https://groups.google.com/g/cerner-fhir-developers/c/JhjhzD9T6C0>
- Synthea FHIR output: <https://github.com/synthetichealth/synthea/wiki/HL7-FHIR>, <https://mitre.github.io/fhir-for-research/modules/synthea-running>
- hl7apy (parser + MLLP server): <https://hl7apy.readthedocs.io/>; python-hl7: <https://python-hl7.readthedocs.io/>
- US Core discharge summary (LOINC 18842-5): <https://hl7.org/fhir/us/core/STU6.1/DocumentReference-discharge-summary.json.html>
