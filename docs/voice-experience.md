# Voice: the Call Room

A voice call is where Continuum's rules are easiest to see: the patient says
something, and the coordinator watches exactly what it changes, and what it
doesn't. Open it from any patient page (**Voice check-in → Open call room**)
or at `/patients/<id>/call`.

## What's on screen

| Column | Shows | Why it matters |
| --- | --- | --- |
| **Brief** | What the agent will raise and *why*, built from open follow-ups and memory: conflicts first, then overdue, then items that need proof. Items tick off as the call covers them. | Memory visibly steers the conversation. |
| **Conversation** | The transcript, with each agent action inline at the moment it happened ("Recorded patient report (unverified…)", "Escalated to a clinician"). | The agent's actions are tied to the words that caused them. |
| **What changed** | Follow-ups moving state (pending → patient reported · needs clinic proof), conflicts kept side by side, decisions queued for coordinators or clinicians, and this call's own ledger entry (intended → sent → confirmed by its transcript). | Nothing is marked done on the patient's word. |

## Three modes

| Mode | Needs | Use it for |
| --- | --- | --- |
| **Simulated** | nothing | Testing and the demo safety net. A scripted call built from the patient's real open follow-ups that runs the **same tool handlers** as a real call (report, escalate, booking request, callback). |
| **Browser** | `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID` | You play the patient through your mic; transcript lines stream from the ElevenLabs SDK into the session. |
| **Phone** | + `ELEVENLABS_PHONE_NUMBER_ID` | ElevenLabs dials a real number (e.g. the presenter's phone on stage). Tool calls appear live; the full transcript arrives with the post-call webhook, which closes the session and confirms the ledger entry. |

## Rules the experience enforces

- The agent says it's an AI and asks consent to record; identity is verified before care is discussed.
- A patient report is recorded as **unverified**; the follow-up waits for clinic proof plus a coordinator sign-off.
- Medication or symptom questions are never answered; they're escalated to the clinician queue.
- The agent never says something is booked; it says the care team will confirm.
- A call counts as done only when a transcript exists; a call that ends with no conversation is marked **failed** in the ledger.
- Patients without consent to automated calls can't be called from here.

## Testing with keys

1. Put `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID` (and `ELEVENLABS_PHONE_NUMBER_ID` for phone) in `.env`; set up the agent per [elevenlabs-setup.md](elevenlabs-setup.md), including the `call_session_id` tool parameter.
2. `cd backend && .venv/bin/python -m app.keys_check`: the ElevenLabs rows should be ✓.
3. Restart the API; the Browser and Phone modes become selectable in the Call Room.

## API

`GET /api/calls/modes` · `GET /api/calls/brief/{patient_id}` · `POST /api/calls` `{patient_id, mode, phone?}` ·
`GET /api/calls/{id}` · `POST /api/calls/{id}/connected` · `POST /api/calls/{id}/transcript` · `POST /api/calls/{id}/end` ·
`GET /api/calls/patient/{patient_id}`. Sessions live in `call_sessions` (streamed over the change stream, covered by PHI retention).
