# ElevenLabs Conversation AI — production setup

Continuum's voice agent is a **general care-coordination agent for any patient
in the clinic's roster** — not tied to one person or one scenario. It
identifies the caller, verifies consent, checks in on open follow-ups, records
patient-reported updates (always unverified), escalates clinical questions to a
human — and, on request, **takes action**: books appointments (for coordinator
confirmation), queues items to send the patient, and schedules callbacks.

The backend exposes everything under `/api/voice/*`:

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/voice/identify` | `x-continuum-token` | Resolve a caller (name + date of birth, or phone) to patient record(s) |
| `GET /api/voice/context/{patient_id}` | `x-continuum-token` | Open follow-ups + preferences for a verified patient |
| `POST /api/voice/report` | `x-continuum-token` | Record a patient-reported update (unverified) |
| `POST /api/voice/escalate` | `x-continuum-token` | Escalate a clinical question / emergency |
| `POST /api/voice/appointment` | `x-continuum-token` | Patient's booking request → coordinator approval |
| `POST /api/voice/follow-up` | `x-continuum-token` | "Send me X" request → coordinator approval |
| `POST /api/voice/checkin` | `x-continuum-token` | Patient-requested callback → scheduled check-in |
| `POST /api/voice/post-call` | `x-continuum-token` | Post-call webhook (transcript ingestion) |
| `GET /api/voice/calls/{patient_id}` | `x-continuum-token` | List recorded calls |

All token-protected routes reject requests without `CONTINUUM_VOICE_SECRET`
being set on both sides — set it before exposing anything publicly.

## 1. Create the agent

1. Open **https://elevenlabs.io/agents** → **Create agent**.
2. Name: `Continuum` · pick a calm, neutral voice.
3. **Agent instructions** — paste this:

```text
You are Continuum, an AI care-coordination assistant working on behalf of a
clinic's care team. You call patients after discharge to check on their
follow-ups. You are not a clinician and you do not provide medical care.
Opening (every call, in this order):
1. Greet and state who you are: "Hi, this is Continuum, an AI care assistant
   calling on behalf of your care team."
2. Disclose clearly that you are an AI, that the call is recorded and
   transcribed, and ask for consent to continue. If the caller does not
   consent, apologize and end the call.
3. Verify identity with two identifiers. Ask for the caller's full name and
   date of birth. Call identify_patient with both (birth_date as YYYY-MM-DD).
   Follow the `guidance` it returns: when it asks for the date of birth, ask
   and call again; do not discuss care until it says identity is verified.
   - No matches: do not retry with guessed or corrected names. Apologise,
     say a member of the care team can call back, and end the call.
   - One match: read the name back and confirm the phone number ending. If
     the match shows consent_to_automated_calls false, do not continue the
     check-in; tell them the care team will reach out and end the call.
   - Multiple matches: ask for the full phone number and call identify_patient
     again. Never reveal that other names or patients exist in the system.
After identity is verified, call get_patient_context with the confirmed
patient_id, then:
- Ask about open follow-ups ONE at a time, in plain language, and listen.
- If the caller says a follow-up is done or scheduled, call
  submit_patient_report with their words. NEVER describe a patient-reported
  update as confirmed or complete. Say it has been "noted for the care team"
  and will be verified with the clinic.
- If the caller reports they did NOT do something, note it and ask what is
  blocking them; record that in the report too.
- Never diagnose, prescribe, adjust medication, or give clinical advice. If
  asked about medication, dosage, symptoms, or treatment, call
  escalate_to_clinician with the question as the reason (urgency "routine"),
  then tell the caller a clinician from the care team will follow up.
- Keep the call under 3 minutes. Close by summarising exactly what was noted
  and what the care team will do next.

Actions (the patient can ask you to do things on the call):
- If the patient asks to book or reschedule an appointment, call
  book_appointment with the time they asked for and the follow-up it is for.
  Afterwards say the request was sent to the clinic and they will confirm the
  time. NEVER say the appointment is booked or confirmed — it is pending the
  clinic.
- If the patient asks the clinic to send them something (a form, medication
  list, reminder, information), call send_follow_up. Afterwards say you have
  asked the care team to send it. NEVER say it has already been sent.
- If the patient asks to be called back at a specific time, call
  request_callback. You may confirm this one directly: "I've set a call for
  Monday morning."
- These are requests actioned by the care team; keep the same privacy and
  no-clinical-advice rules as everything else on the call.

Privacy and conduct at all times:
- Discuss only the verified patient's own care. If a family member or
  caregiver calls, verify the patient's name and the caller's relationship
  before sharing anything; when in doubt, offer a callback from the care team.
- Never read out full phone numbers, MRNs, or other patients' information.
- If the caller reports a medical emergency (chest pain, trouble breathing,
  collapse, heavy bleeding, etc.), tell them to call 911 immediately and call
  escalate_to_clinician with urgency "emergency".
- If the caller is distressed or asks to speak to a human, offer a callback
  from the care team and end politely.
```

4. **Tools** — create seven tools. `<PUBLIC_URL>` is your public backend URL
   (section 2). In the dashboard, each tool is a **webhook** with a JSON
   schema; every tool sends the static header
   `x-continuum-token: <your CONTINUUM_VOICE_SECRET>` (in the API this field
   is `request_headers` — the old `headers` name is ignored). If you create
   them via API (`POST /v1/convai/agents/create` tools), the per-tool body
   shape is:

   ```json
   {
     "name": "<tool_name>",
     "tool_config": {
       "type": "webhook",
       "name": "<tool_name>",
       "description": "<what to call it and when>",
       "api_schema": {
         "url": "https://<PUBLIC_URL>/api/voice/<endpoint>",
         "method": "POST",
         "request_headers": { "x-continuum-token": "<secret>" },
         "request_body_schema": {
           "type": "object",
           "properties": { "field": { "type": "string", "description": "..." } },
           "required": ["..."]
         }
       }
     }
   }
   ```

   Then attach them to the agent: `PATCH /v1/convai/agents/{agent_id}` with
   `{"conversation_config": {"agent": {"prompt": {"tool_ids": [...]}}}`.

   The seven tools:

   | Tool | Endpoint | Required params |
   |---|---|---|
   | `identify_patient` | `POST /api/voice/identify` | `given_name` (optional `family_name`, `birth_date` as YYYY-MM-DD, `phone`) |
   | `get_patient_context` | `GET /api/voice/context/{patient_id}` | `patient_id` (path param — declare it in `path_params_schema`) |
   | `submit_patient_report` | `POST /api/voice/report` | `patient_id`, `report` (optional `obligation_id`, `reported_status`) |
   | `escalate_to_clinician` | `POST /api/voice/escalate` | `reason` (optional `patient_id`, `urgency`) |
   | `book_appointment` | `POST /api/voice/appointment` | `patient_id`, `requested_time` (optional `obligation_id`, `notes`) |
   | `send_follow_up` | `POST /api/voice/follow-up` | `patient_id`, `item` (optional `reason`) |
   | `request_callback` | `POST /api/voice/checkin` | `patient_id`, `when` (optional `reason`) |

   **Call Room:** give every POST tool an optional `call_session_id` body
   parameter whose value is the dynamic variable `{{call_session_id}}`. The
   console passes it when it starts a browser or phone call, so each tool call
   (and what it changed) appears live on that call. Without it, the backend
   falls back to the patient's live call, if any.

   Tool descriptions that work well (they carry the guardrail language):
   - `book_appointment`: "Request to book an appointment for the verified
     patient. The clinic confirms the time afterwards — after calling this,
     tell the patient the request was sent and the clinic will confirm. NEVER
     say the appointment is booked."
   - `send_follow_up`: "Ask the care team to send the patient something they
     requested. After calling this, tell the patient it has been requested —
     never say it has already been sent."
   - `request_callback`: "Schedule a callback: the care team will call the
     patient at a time they choose. This is confirmed immediately on the call."

## 2. Make the backend reachable

Options, in order of permanence:

- **Tailscale Funnel** (tailnet node): `tailscale funnel 8000` → prints
  `https://<node>.<tailnet>.ts.net`.
- **Cloudflare quick tunnel** (no account, ephemeral URL):
  ```bash
  cloudflared tunnel --url http://localhost:8000
  ```
  prints a `https://<random>.trycloudflare.com` URL. It changes on every
  restart — fine for verification, not for production.
- **Docker on a VPS** (production shape, see `docs/deployment.md`).

Whatever it is, test from outside your network (phone on mobile data):
`https://<PUBLIC_URL>/api/health` should return JSON.

## 3. Webhook (post-call transcript → agent pipeline)

Post-call webhooks are **workspace webhooks** wired into the ConvAI settings
(via dashboard *Settings → Webhooks*, or API):

```bash
# 1. create the webhook (HMAC variant supports static request headers)
curl -X POST https://api.elevenlabs.io/v1/workspace/webhooks \
  -H "xi-api-key: $KEY" -H "Content-Type: application/json" -d '{
    "settings": {
      "auth_type": "hmac",
      "name": "Continuum post-call",
      "webhook_url": "https://<PUBLIC_URL>/api/voice/post-call",
      "request_headers": { "x-continuum-token": "<secret>" }
    }
  }'
#    → {"webhook_id": "…", "webhook_secret": "wsec_…"}

# 2. wire it into the ConvAI settings
curl -X PATCH https://api.elevenlabs.io/v1/convai/settings \
  -H "xi-api-key: $KEY" -H "Content-Type: application/json" -d '{
    "webhooks": {
      "post_call_webhook_id": "<webhook_id>",
      "events": ["transcript"],
      "transcript_format": "json"
    }
  }'
```

Copy the returned `wsec_…` secret into `ELEVENLABS_WEBHOOK_SECRET` in `.env`
(the backend then verifies webhook signatures). For a single-patient pilot you
can append `?patient_id=<id>` to the URL; otherwise the backend resolves the
patient from the conversation during sync.

## 4. Phone number (or widget)

- **Dashboard test panel (fastest to try):** open the agent — a built-in
  text/voice test panel runs the real pipeline against your backend.
- **Widget:** Agents page → **Get widget** → embed in any page.
- **Real number:** Agents page → **Phone number** → buy one. Inbound calls
  reach the agent; outbound check-in campaigns use the same agent.

## 5. Fill `.env` and restart

```env
ELEVENLABS_API_KEY=sk_...                 # your API key
ELEVENLABS_AGENT_ID=agent_xxx             # from the agent's settings page
CONTINUUM_VOICE_SECRET=<random 32+ chars> # you choose; also the x-continuum-token
ELEVENLABS_WEBHOOK_SECRET=wsec_...        # from the webhook creation step
ELEVENLABS_PHONE_NUMBER=+1xxxxxxxxxx      # only if you buy a number
ELEVENLABS_PHONE_NUMBER_ID=phnum_...      # Agents → Phone numbers; required for outbound calls
CALL_TIMEZONE=America/Toronto             # calling hours are local to this zone
```

Restart the backend. `/api/health` should show `"voice_configured": true`.

## 6. Verify end-to-end

**A. Simulation test (no phone number needed)** — the dashboard's *Run test*
feature via API: create a simulation test
(`POST /v1/convai/agent-testing/create` with `type: "simulation"`, a
`simulation_scenario` describing the patient persona and a scripted
conversation, and `success_conditions` asserting the guardrails), then run it
(`POST /v1/convai/agents/{agent_id}/run-tests`) and poll
`GET /v1/convai/test-invocations/{id}`. Tools are **not mocked** unless you
pass `tool_mock_config`, so every tool call hits the real backend. Worth
asserting: consent before care details, identity via `identify_patient`,
reports recorded unverified, booking requests never claimed as booked,
medication questions escalated with no advice.

**B. Real call** — call the number (or use the widget/test panel):
*"It's Michael Chen, my number ends in 0142. I went to see Dr. Shah on
Thursday, everything was fine. Can you book the cardiology follow-up for
Friday, and call me on Monday about the bloodwork?"*

Then confirm:
1. `GET https://<PUBLIC_URL>/api/voice/calls/<patient_id>` (token header) —
   the call is listed with a transcript.
2. Console (coordinator): the physician visit shows a **patient-reported**
   update (not verified); the Approvals queue has **"Book appointment:
   Cardiology follow-up — …"** and **"Send … to …"** cards; approving the
   booking card moves the obligation to `scheduled` with the confirmed date;
   the audit trail shows `Voice appointment requested`, `Voice callback
   scheduled`, `Voice report submitted`.
3. Negative tests worth running once: a name not in the system → polite
   no-match, no retry loop; a medication question → escalation, no advice;
   an emergency statement → 911 instruction + emergency escalation.

**C. Outbound check-in call (Continuum calls the patient)**

Prerequisites: steps 1, 4 and 5 done, with `ELEVENLABS_API_KEY`,
`ELEVENLABS_AGENT_ID` and `ELEVENLABS_PHONE_NUMBER_ID` set. The phone in the
test must be one you answer; if your ElevenLabs/Twilio number is a trial
number, it may only be allowed to call verified numbers.

1. **Make yourself the patient.** Console → **Patients → New**
   (`/patients/new`): your phone **with its country code** (`+1 416 …`),
   preferred channel **Voice call**, and tick **consent to automated calls**.
   Continuum only dials when all three hold: another channel becomes a
   "Contact … by Text/Email/Portal" coordinator task, and missing consent
   means no call. Seeded numbers end in "(synthetic)" and are never dialled.
2. **Create a check-in that's due now** (replace `<patient_id>` with the id from
   the patient's URL):
   ```bash
   curl -s -H 'content-type: application/json' -H 'x-continuum-token: <CONTINUUM_VOICE_SECRET>' \
     -d '{"patient_id":"<patient_id>","when":"now","reason":"Test call"}' http://localhost:8000/api/voice/checkin
   ```
3. **Run the sweep with the real clock:** `POST /api/admin/sweep` as a
   coordinator, or the console's **Run sweep** with the days-ahead selector
   on **0**. Any days-ahead value above 0 fast-forwards to 23:59 UTC on a future
   day, which is not a real calling time. Look at `actions` in the response:
   - `"type": "checkin", "status": "dispatched"`: the call was placed; your phone rings.
   - `"type": "checkin_held"`: you're outside calling hours. `calling_hours`
     shows the window and `calls_after` when it will go out. Use
     `CALL_TIMEZONE` for your own zone, or for a quick test set
     `CALL_WINDOW_START_HOUR` = `CALL_WINDOW_END_HOUR` to disable the window.
   - `"status": "awaiting_manual_call"`: no call went out. The check-in's
     `dispatch_detail` says why: no dialable number, ElevenLabs rejected the
     request (4xx: check the phone number id and agent), or the outcome is
     unknown (timeout/5xx: **check ElevenLabs' call log before calling the
     patient again**; Continuum will not redial on its own).
4. **Confirm the loop closed.** The action ledger (`GET /api/actions`) shows
   the call as `sent` with the ElevenLabs conversation id. It becomes
   `confirmed` when the post-call webhook (section 3) delivers the transcript,
   which needs the public URL from section 2. Without the webhook the call is
   flagged `unconfirmed` after 6 hours; that is intended, not a failure.

Each check-in is dialled at most once. To test again, create a new check-in
(step 2).
