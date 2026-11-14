# Continuum

Persistent-memory care coordination for the gap after hospital discharge.

Continuum turns a discharge summary into tracked follow-up obligations, keeps
every loop open until it is backed by evidence or an authorized decision, and
remembers what happened across documents, chat and voice calls. A coordinator
console shows the work, the reasoning trail and every approval.

Built with FastAPI, LangGraph, MongoDB Atlas, ElevenLabs and React/TypeScript.

> Continuum is an administrative coordination tool. It does not diagnose,
> prescribe or change treatment. All bundled patient data is synthetic.

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Roles](#roles)
- [Product rules enforced in code](#product-rules-enforced-in-code)
- [API](#api)
- [Demo scenario](#demo-scenario)
- [Evaluation](#evaluation)
- [Testing](#testing)
- [Repository layout](#repository-layout)

## Features

- **Patient intake.** Create a patient, care episode and follow-up obligations
  from a form, or paste a discharge summary and let the agent extract the
  follow-ups.
- **Unclosed Loop Engine.** Patient reports are evidence, never verification.
  Sensitive closures need proof or an explicit override, contradictions are
  preserved, and history is append-only.
- **Human-in-the-loop approvals.** Sensitive recommendations become queued
  approvals. An explicit in-chat request triggers a LangGraph `interrupt()`
  that pauses the patient's thread until the approval is resolved.
- **Persistent memory.** Cross-session memories are recalled through
  LangChain's `MongoDBAtlasVectorSearch`, filtered per patient, with a lexical
  fallback when no vector index exists. LangGraph checkpoints are stored in
  MongoDB, so paused runs survive restarts.
- **Voice check-ins.** An ElevenLabs Conversational AI agent identifies the
  caller, asks for consent, checks in on open follow-ups, records
  patient-reported updates as unverified and escalates clinical questions.
  Booking and "send me X" requests are queued for the care team; the agent
  never claims something is booked or sent until a person confirms it.
- **Proactive follow-through.** A background sweep places due check-ins, chases
  overdue follow-ups and requests proof for stale patient reports. It is
  idempotent, consent-aware and respects a configurable calling window.
- **Text, email and portal check-ins.** Patients can be reached by SMS
  (Twilio), email (SMTP) or a portal link, and urgent escalations page the
  on-call clinician until someone acknowledges.
- **EHR ingestion.** An HL7 `ADT^A03` message or a manual pull makes Continuum
  fetch the discharge from a FHIR R4 server. Referrals, labs and booked
  appointments become follow-ups, and a poller matches results to their
  orders. SMART Backend Services (signed JWT) auth is supported.
- **Clinic evidence intake.** Clinic and lab systems can post FHIR resources
  to confirm bookings, finished visits and final results.
- **Transitional care tracking.** Episodes, the TCM contact and visit windows,
  and a per-coordinator work queue.
- **Audit and inspection.** An append-only audit log and an inspector that
  shows the checkpoint, extraction trace and approval behind each agent reply.

## Architecture

```text
React coordinator console (frontend/)
        │
        │  /api  (JWT, role-scoped)
        ▼
FastAPI (backend/app)
  ├── routes/          patients, obligations, agent, voice, calls, ingest,
  │                    integrations, review, tcm, work, portal, auth, meta
  ├── engine.py        Unclosed Loop Engine: validation, proposals, approvals
  ├── repository.py    MongoDB persistence, derived statuses, audit
  ├── scheduler.py     Follow-through sweep
  ├── ingest/          HL7 and FHIR ingestion, result poller
  └── agent/
        ├── graph.py       LangGraph: ingest → extract → retrieve → validate
        │                  → propose → apply_auto → await_approval → compose
        ├── extraction.py  LangChain structured output with deterministic rules
        ├── memory.py      Atlas Vector Search recall with lexical fallback
        └── checkpoint.py  MongoDBSaver, one thread per patient
        │
        ├── MongoDB Atlas   patients, episodes, obligations, evidence, memories,
        │                   approvals, audit, voice calls, checkpoints
        └── ElevenLabs      voice agent tools and post-call webhook
```

Every channel (document, coordinator chat, live voice transcript and post-call
webhook) runs the same graph and writes through the same rules.

## Getting started

### Prerequisites

- Python 3.11 or later
- Node.js 20 or later (for the console)
- Optional: a MongoDB Atlas cluster, an LLM API key and an ElevenLabs account

### Run locally with no external services

The app runs end to end with an in-memory database and deterministic
extraction, so no keys are needed for a first run.

```bash
cp .env.example .env          # CONTINUUM_DB_MODE=memory is the default

# Build the console (outputs to dist/ at the repo root)
cd frontend
npm install
npm run build

# Start the API, which also serves the console
cd ../backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

Open <http://localhost:8000> and sign in with one of the demo accounts:

| Role | Email | Password |
| --- | --- | --- |
| Coordinator | `coordinator@continuum.demo` | `continuum` |
| Clinician | `clinician@continuum.demo` | `continuum` |

These accounts are for demos only. Change them through the `DEMO_*` settings
and set a strong `AUTH_SECRET` before any real deployment.

### Frontend development

Vite serves the console on port 5173 and proxies `/api` to FastAPI on port
8000.

```bash
cd frontend
npm run dev
```

### Docker

The backend image is built from the repository root:

```bash
docker build -f backend/Dockerfile -t continuum-api .
docker run --env-file .env -p 8000:8000 continuum-api
```

The image contains the API only. Serve the console separately, for example
from the Vite build output.

### Voice agent

With `ELEVENLABS_API_KEY` set in `.env` and the API reachable at a public
HTTPS URL:

```bash
backend/.venv/bin/python scripts/create_voice_agent.py https://<public-url>
backend/.venv/bin/python scripts/point_voice_agent.py https://<public-url>
```

The first script creates the agent and its webhook tools and writes
`ELEVENLABS_AGENT_ID` to `.env`. The second sets up the post-call webhook; run
it again whenever the public URL changes.

## Configuration

All settings are environment variables, documented in
[.env.example](.env.example). The main groups are:

| Group | Key settings | Notes |
| --- | --- | --- |
| Database | `CONTINUUM_DB_MODE`, `MONGODB_URI`, `MONGODB_DB` | `memory` uses an in-process mock; `atlas` uses MongoDB Atlas |
| LLM | `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` | OpenAI-compatible, Anthropic or Gemini. With no key, extraction is deterministic |
| Embeddings | `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | Follows the LLM provider, or falls back to a local CPU model |
| Voice | `ELEVENLABS_*`, `CONTINUUM_VOICE_SECRET`, `CALL_*` | Outbound calls need `ELEVENLABS_PHONE_NUMBER_ID` |
| Messaging | `TWILIO_*`, `SMTP_*`, `ON_CALL_*` | SMS and email check-ins, on-call paging |
| EHR | `EHR_FHIR_BASE_URL`, `EHR_AUTH_MODE`, `INTEGRATION_SECRET` | Discharge pulls, result polling and clinic evidence intake |
| Scheduler | `SCHEDULER_INTERVAL_SECONDS` | `0` disables the background sweep |
| Auth | `AUTH_SECRET`, `DEMO_MODE`, `DEMO_*` | JWT sign-in and demo accounts |
| Retention | `PHI_RETENTION_DAYS` | Days before operational records become purge-eligible |
| Tracing | `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Tracing turns on when a key is set |

Never commit a real `.env` file. It is excluded by `.gitignore`.

## Roles

| Capability | Coordinator | Clinician |
| --- | :-: | :-: |
| View patients, journeys and the audit log | Yes | Yes |
| Create patients | Yes | Yes |
| Approve closures, conflicts, cancellations and overrides | Yes | No |
| Resolve clinical escalations | No | Yes |
| Run the retention report and apply it | Yes | No |

Each approval carries an audience, and the list and resolve endpoints enforce
it. A user acting outside their role receives a 403.

## Product rules enforced in code

These are validation rules in `backend/app/engine.py` and status checks in
`backend/app/routes/`, not prompt suggestions.

- **A patient report is evidence, not completion.** A report moves a loop to
  `patient_reported` and asks for verification.
- **Sensitive closures need proof or an override.** `verified`, `cancelled`
  and `escalated` require an evidence summary, a clinic reference or an
  explicit coordinator override.
- **Contradictions are preserved.** A conflicting value is stored as a
  conflict. Both values stay visible, and resolution requires approval.
- **History is append-only.** Status changes write evidence, obligation
  history and audit records with before and after values.
- **No clinical advice.** Medication and symptom questions are declined,
  escalated to the clinician queue and recorded.
- **Voice disclosures are explicit.** The agent identifies itself as an AI,
  asks consent to record, verifies identity before discussing care and
  respects per-patient consent flags.

## API

All endpoints are under `/api`. Interactive documentation is available at
<http://localhost:8000/docs> when the server is running.

| Area | Prefix | Purpose |
| --- | --- | --- |
| Auth | `/api/auth` | Sign-in and the current user |
| Patients | `/api/patients` | Intake, journeys, documents, episodes, memories |
| Obligations | `/api/obligations` | Status changes, evidence, clinic confirmation, assignment |
| Agent | `/api/agent` | Coordinator chat, threads, approvals, audit |
| Voice | `/api/voice` | Voice agent tools and the post-call webhook (token-gated) |
| Calls | `/api/calls` | Call sessions and live transcripts |
| Ingestion | `/api/ingest` | HL7 messages, FHIR pulls, jobs and the result poller |
| Integrations | `/api/integrations` | Clinic and lab FHIR R4 intake |
| Review, TCM, Work | `/api/review`, `/api/tcm`, `/api/work` | Review queue, transitional care windows, work queue |
| Admin | `/api/admin` | Follow-through sweep and retention |
| Health | `/health`, `/api/health` | Liveness probe and detailed status |

## Demo scenario

With `DEMO_MODE=true` (the default), the app seeds a sample roster and a
five-day scenario for a synthetic patient discharged after decompensated
heart failure.

| Day | Channel | What happens | Capability shown |
| --- | --- | --- | --- |
| 0 | Document | Discharge summary creates four obligations | Extraction and structured tracking |
| 3 | Voice | Patient says he saw his family physician | Cross-session memory; a report is not verification |
| 7 | Dashboard | Coordinator confirms cardiology with a clinic record | Evidence-based status change |
| 10 | Voice | Patient gives a different cardiology date | Contradiction preserved; approval required |
| 12 | Voice | Patient asks about changing a medication dose | Guardrails and clinician escalation |

Replay it from the demo runner on the dashboard. Set `DEMO_MODE=false` for a
live deployment.

## Evaluation

The evaluation suite scores Continuum against a stateless baseline (the same
extractor with no memory and no loop engine) on 21 synthetic cases.

```bash
cd backend
.venv/bin/python -m evals.run               # prints a table, writes evals/results/
.venv/bin/python -m evals.run --langsmith   # also uploads the dataset and experiments
```

| Metric | Continuum | Stateless baseline |
| --- | --- | --- |
| Follow-ups extracted from discharge text (recall) | 100% | 100% |
| Extracted follow-ups that were really ordered (precision) | 100% | 100% |
| Follow-ups in the correct state at the end | 100% | 15% |
| Still-open follow-ups still tracked | 100% | 2% |
| Patient self-reports not treated as proof | 100% | 60% |
| Date contradictions caught and preserved | 100% | 0% |
| Clinical and medication questions escalated correctly | 100% | 100% |
| Clinic-confirmed dates remembered | 100% | 50% |

These figures come from the deterministic extractor with no LLM key. The full
report is in
[backend/evals/results/latest.md](backend/evals/results/latest.md).

## Testing

Backend tests use an in-memory database and deterministic extraction, so they
need no keys or network access.

```bash
cd backend
.venv/bin/python -m pytest tests/ -q
```

The frontend is type-checked as part of `npm run build`.

## Repository layout

```text
backend/
  app/            FastAPI application, loop engine, agent, ingestion
  evals/          Evaluation suite and saved results
  tests/          Backend test suite
  Dockerfile
  requirements.txt
frontend/         React + TypeScript coordinator console (Vite)
scripts/          ElevenLabs voice agent setup
.env.example      Configuration template
```
