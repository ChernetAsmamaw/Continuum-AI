# Continuum compliance posture

> **Read this first.** This document describes the *posture* Continuum ships
> with and the work still required before a regulated clinic can put it in
> production. It is an engineering checklist, **not legal advice**. A clinic's
> privacy officer or counsel must sign off, and where Continuum processes PHI
> under HIPAA (or a provincial/Canadian equivalent), the items in §5 are
> prerequisites, not suggestions.

## 1. What the system is and isn't

Continuum is an **administrative care-coordination** tool. It tracks
post-discharge follow-ups, records patient-reported updates (never as
verified), and routes sensitive decisions to humans. It:

- does **not** diagnose, prescribe, or recommend treatment;
- does **not** store clinical notes, lab values, or imaging — it stores
  *coordination state* (who, what follow-up, by when, what was reported, who
  approved what);
- treats every patient-reported statement as **unverified** until a human
  confirms it against the source of record.

The least sensitive possible classification still applies: patient names,
conditions, phone numbers, and follow-up details are **PHI**. Handle it as
such.

## 2. PHI inventory

| Data | Where | Notes |
|---|---|---|
| Patient identity: name, age, MRN, pronouns, phone, caregiver | `patients` | Core PHI; long-lived |
| Discharge reason, admission/discharge dates, summary text | `care_episodes` | PHI; the summary is free text pasted at intake |
| Follow-up descriptions, owners, due dates, status | `obligations` | Derived from discharge summaries |
| Patient-reported statements, clinic confirmations | `evidence`, `events` | PHI |
| Call transcripts (what patients said) | `voice_calls` | **Most sensitive** — verbatim PHI |
| Free-text memories of the patient | `memories` | LLM-processed; may quote transcripts |
| Agent decisions and human approvals | `agent_audit`, `approvals` | Names of staff + patient context |
| Staff accounts (email, name, role) | `users` | Employee PII, not patient PHI |

**Not stored:** clinical notes, diagnoses beyond the free-text `condition`,
lab values, insurance data, payment data.

## 3. Data flows (where PHI leaves the box)

```
console (HTTPS, JWT) ──► FastAPI ──► MongoDB Atlas   ← PHI at rest
                              │
                              ├─► LLM gateway (OpenAI-compatible)
                              │     PHI sent: document text + extraction prompt
                              │     PHI returned: extracted obligations (JSON)
                              │
                              └─► ElevenLabs (voice)
                                    PHI sent: patient context for the call,
                                    transcripts on post-call webhook
                                    PHI returned: transcripts + tool results
```

Every arrow that crosses a provider boundary needs a contract. See §5.

## 4. Technical controls already implemented

- **Authentication**: JWT (HS256, configurable TTL) over HTTPS; PBKDF2-SHA256
  password hashing (200k iterations). `AUTH_SECRET` must be rotated to a
  strong random value in production.
- **Role-based access control**: two roles with different queues and
  permissions —

  | Capability | Coordinator | Clinician |
  |---|---|---|
  | View patients, journeys, audit | ✅ | ✅ |
  | Patient intake (create patients) | ✅ | ✅ |
  | Approve closures / conflicts / cancellations / overrides | ✅ | ❌ (403) |
  | Resolve clinical escalations | ❌ (403) | ✅ |
  | Admin: retention report + apply | ✅ | ❌ (403) |

  Approval documents carry an `audience`; list/resolve endpoints filter and
  enforce it. The dashboard and nav badge count only the caller's queue.
- **Voice API**: token-gated (`x-continuum-token`, constant-time compare);
  the identify endpoint returns only identity-verification fields (never MRN
  or conditions) and the agent prompt forbids reading out full identifiers.
- **Audit logging**: append-only `agent_audit` (every obligation change,
  approval decision, voice action, intake) + per-obligation `history` —
  state is never overwritten; conflicts preserve both values.
- **Consent at the point of call**: the voice agent discloses it is an AI,
  asks for recording consent, and checks per-patient
  `consent_to_automated_calls` / `consent_to_recording` before continuing.
- **Retention**: `PHI_RETENTION_DAYS` (default 365) drives a sweep over the
  high-churn collections (`events`, `evidence`, `voice_calls`, `agent_audit`,
  `memories`, `checkins`). Dry-run report at `GET /api/admin/retention`,
  apply at `POST /api/admin/retention/apply`, both coordinator-only. Core
  coordination records (patients, episodes, obligations + history) are kept —
  they are the operational record of care.
- **Least-privilege LLM exposure**: the LLM only ever receives the document
  text being extracted and generic instructions; it has no tool access to
  the database.

## 5. Contracts & third-party checklist (blocking for production)

| Provider | What it sees | Required |
|---|---|---|
| **MongoDB Atlas** | All PHI at rest | BAA (Atlas Enterprise; free/Shared tiers do **not** offer a BAA) or equivalent DPAs for non-US jurisdictions; TLS in transit (on by default); encryption at rest (on); IP allowlist / private endpoint |
| **LLM gateway** (vLLM / OpenAI / Groq / …) | Document text sent for extraction | BAA (OpenAI offers one on business plans); if self-hosted on your own infrastructure, no data leaves your boundary — **preferred** |
| **ElevenLabs** | Patient context, live audio, transcripts | BAA (available on enterprise); otherwise a contractual DPIA — a self-hosted TTS/STT stack removes this boundary entirely |
| **The clinic's EHR (future)** | Import of discharge data | Data-sharing agreement; Continuum should remain a *downstream consumer*, never the system of record |

If a provider cannot sign a BAA/DPA, either replace it (self-host) or stop
sending PHI that way. There is no partial-compliance middle ground for a
production clinic.

## 6. Known gaps (work needed before regulated use)

1. **No SSO/SAML/OIDC, no MFA.** JWT + password is the only auth. Clinics
   will require SSO (Okta/Entra/Google) and MFA.
2. **User management is seed-based.** There is no admin API to create,
   disable, or rotate staff accounts. (The two `DEMO_*` accounts exist for
   the pilot; a real deployment needs provisioning.)
3. **No data-subject-request workflow** (access/correct/delete for a
   patient's own record) — the retention sweep is time-based only.
4. **No per-patient access control for family/caregivers** — the caregiver is
   recorded as a free-text field; the voice agent handles them verbally, but
   there is no logged-in caregiver portal or formal information-sharing
   agreement (ROPA/authorization-on-file) mechanism.
5. **Audit log is not exportable** (no CSV/PDF export, no SIEM forward).
6. **No formal incident-response / breach-notification runbook** in-repo.
7. **Synthetic data is still present** by default (`auto_seed`). A real
   deployment should seed nothing and rely on intake.
8. **No key management/rotation tooling** for `AUTH_SECRET` (JWTs must be
   invalidated on rotation) or the voice secret.
9. **Backups are not automated** in-repo (use Atlas backups + tested restore).
10. **No encryption of the DB URI in-repo** — it sits in `.env`; ensure file
    permissions (0600) and secrets-manager injection in production.

## 7. Suggested rollout order for a real clinic

1. Self-host (or tailnet-internal) LLM → removes the largest data-exfil
   boundary.
2. Atlas Enterprise + BAA (or on-prem MongoDB).
3. ElevenLabs enterprise BAA, or defer voice until it's signed.
4. Flip `DEMO_MODE=false`, delete synthetic users/patients, set
   `AUTH_SECRET` + `CONTINUUM_VOICE_SECRET` to fresh random values.
5. Add SSO/MFA (§6.1) behind a reverse proxy that can enforce it.
6. Adopt the retention policy as written, run the dry-run monthly, apply
   quarterly — and record each run in the clinic's own audit trail.
