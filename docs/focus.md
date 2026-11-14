# What to focus on

Continuum is built for **Statement Three: Finish the Job** of Agents in Context
(MongoDB, Toronto, Nov 14 2026). The brief asks for an agent that completes a
long, multi-step task across several systems, using MongoDB Atlas for verified
task state and Vector Search for past experience, so it survives crashes and
system changes and knows what actually happened.

Every demo minute should answer one of the brief's three questions.

## The three questions

| Question | Continuum's answer | Code | Demo moment |
|---|---|---|---|
| **How does it tell an action that took effect from one that was only sent?** | Action ledger: every outbound action is `intended → sent → confirmed` (only with evidence) or `unconfirmed / failed / unknown`. Reconciliation flags anything sent and never confirmed. Patient reports stay unverified until the clinic confirms them. | `backend/app/ledger.py`, `scheduler.py`, `calls.py` | A voice call: the request shows as *sent*, then *confirmed* when the transcript arrives. The Actions page shows the states. |
| **How does it resume after a crash without replaying its history?** | Checkpointed ingestion jobs (`pull → identify → structured → narrative → verify`) with leases and dead-owner reclaim; change streams resume from a saved token; LangGraph checkpoints in Atlas. | `backend/app/ingest/jobs.py`, `streams.py`, `agent/checkpoint.py` | Send a discharge, crash the job after a step, restart: it continues from the next step. |
| **How does it adapt when a system's layout, fields or workflow change?** | Order memory with Atlas Vector Search: a referral that arrives under a new code or wording is matched to past orders by meaning, and a coordinator's correction is learned for next time. | `backend/app/ingest/orders.py`, `ingest/scenarios.py` | Send a discharge whose order codes changed: it is still recognized, with the match score. |

## The task the agent finishes

A patient's 30-day transition after hospital discharge:

1. **EHR discharge arrives** (HL7 ADT^A03 over MLLP or `POST /api/ingest/hl7`).
2. **FHIR pull** of the patient, encounter, orders, appointments, medications
   and discharge summary; the agent turns them into follow-ups with deadlines.
3. **Follow-through**: voice calls (ElevenLabs), text/email/portal check-ins,
   bookings and requests queued for coordinator approval.
4. **Result poller** checks the EHR for results and appointments and closes
   follow-ups only with evidence.
5. **TCM clock**: contact within 2 business days, visit within 7 or 14 days,
   closed after 30 days.

Systems involved: EHR (HL7 + FHIR), ElevenLabs voice, messaging, MongoDB Atlas,
Gemini, LangSmith.

## What judges will look for

1. **The three answers, live**, each in under a minute.
2. **Real work across real systems**: discharge in, follow-ups created, the
   patient called, the console updating live from Atlas change streams.
3. **Depth of Atlas use**: verified task state, `$jsonSchema` validators,
   change streams, Vector Search, checkpoints, the audit trail. Keep Atlas
   open on the collections during the demo.
4. **Sponsor tech used for real**: LangSmith traces of each agent run
   (project `continuum-hackathon`), the ElevenLabs voice agent.
5. **Trust**: never claims something is done without evidence, never gives
   clinical advice, escalates instead, discloses it is an AI and asks consent.

## Status

| Area | State |
|---|---|
| Atlas (sandbox Cluster0) | Connected; indexes, validators, Vector Search, change streams live |
| LLM | Gemini `gemini-3.5-flash-lite` |
| LangSmith | Connected; traces in `continuum-hackathon` |
| ElevenLabs agent | Created with 7 tools; consent, wrong-DOB, emergency and full check-in scenarios pass |
| Real phone calls | Need a Twilio number imported into ElevenLabs (`ELEVENLABS_PHONE_NUMBER_ID`) |
| Automated EHR fetch | Being brought up against the local HAPI FHIR server with the HL7 listener on |
| Public URL for voice tools | Cloudflare quick tunnel; re-point with `scripts/point_voice_agent.py` when it changes |

## Open issues being fixed

- Escalation reports success when the clinician page failed.
- Callback times like "Friday afternoon" aren't resolved to a date.
- A second escalation merges into an open one and its reason is lost.
- Some tool replies mix agent instructions into the patient-facing line.
- Tool calls don't carry the ElevenLabs conversation id.
- The console token appears in access logs for the live stream.

## Submission checklist (due 5:00 PM)

- [ ] Public GitHub repo (this one) with a short README
- [ ] 1-minute demo video with audio, showing the three answers
- [ ] Short project description on the Cerebral Valley platform
- [ ] All team members added to the submission
- [ ] Remove `0.0.0.0/0` from the Atlas access list after the event
