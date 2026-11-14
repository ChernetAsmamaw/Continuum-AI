# Security and safety review

Branch `claude/safety-pass-39ea7b`, 2026-11-14. Scope: auth, the proactive
scheduler and voice routes (fixed here), plus a read-only audit of EHR
ingestion, the action ledger, change streams/SSE and clinic integrations
(owned by other work streams, so this review gives patches and does not apply them).

Synthetic data only. This is an engineering review, not a compliance sign-off
(see `docs/compliance.md`).

**Tests:** `backend/tests/test_safety.py` (68 new tests) plus updates to
`test_scheduler.py` and `test_resilience.py`. Full suite: 161 passed, 2 skipped.
Against Atlas Local, `test_atlas_live.py` and `test_resilience.py`: 12 passed.

---

## Part 1 — Fixed on this branch

| # | Severity | Issue | Fix | Test(s) |
|---|---|---|---|---|
| F1 | **High** | `GET /api/auth/demo` returned both demo passwords to anyone, even with `DEMO_MODE=false`. | Returns 404 unless demo mode is on, and is left out of the OpenAPI schema. With demo mode off, accounts that would use the public default password are **not created**. | `test_demo_credentials_are_hidden_when_demo_mode_is_off`, `test_default_password_demo_accounts_not_created_outside_demo_mode` |
| F2 | **High** (patient safety) | The scheduler called patients whose preferred channel was Text, Email, Portal or Caregiver. | It dials only when the channel is `"Voice call"` (or not set, which means voice, the schema default). Any other channel moves the check-in to `awaiting_outreach` and queues "Contact <name> by <channel>" through `_queue_task`, so the ledger records a `coordinator_task`. | `test_non_voice_channel_is_never_dialled[Text/Email/Portal/Caregiver]`, `test_voice_channel_still_dials`, `test_missing_channel_defaults_to_voice` |
| F3 | **High** (patient safety) | Calls placed at any hour. A check-in with only a date becomes due at 00:00 UTC, and the next 5-minute sweep would ring the patient around midnight. | Automated calls go out only between `CALL_WINDOW_START_HOUR` and `CALL_WINDOW_END_HOUR` (default 09–20) in `CALL_TIMEZONE` (default `America/Toronto`). Outside that window the check-in stays `scheduled` and the first sweep inside the window places the call. Setting start = end turns the window off. | `test_no_automated_calls_at_night`, `test_calling_window_logic` |
| F4 | **High** | Missing consent counted as consent: `prefs.get("consent_to_automated_calls", True)` in the scheduler and in the voice callback/identify paths. | Consent now has to be explicitly `True` (`scheduler.py`, `workflow.py`). | `test_missing_consent_flag_means_no_automated_call` |
| F5 | **High** | `POST /api/voice/calls/{id}/sync` needed no auth. It used our ElevenLabs key to fetch any conversation and returned the full transcript. The id was also put into the upstream URL unchecked (`%3F` became a query string). | Requires a signed-in user, and the id must match `^[A-Za-z0-9_-]{1,128}$`. | `test_call_sync_requires_sign_in_and_a_sane_id` |
| F6 | **High** | With no `CONTINUUM_VOICE_SECRET`, the whole voice API was open: anyone could look up a patient by name (`/voice/identify`), read their context, or file reports and escalations. | Outside development (`ENVIRONMENT` not in development/dev/local/test), the voice API returns 503 until the secret is set. | `test_voice_api_fails_closed_outside_development` |
| F7 | Medium | The post-call webhook skipped the ElevenLabs signature check when the header was missing (`if secret and header`), so leaving the header off bypassed it. | When `ELEVENLABS_WEBHOOK_SECRET` is set, `/voice/post-call` rejects a request with no signature (401). | `test_post_call_requires_signature_when_webhook_secret_is_set` |
| F8 | Medium | Running with the public default `AUTH_SECRET` let anyone who has read the repo mint a coordinator token. | `config.harden()` runs at startup. Outside development it swaps the default for a random per-process secret (tokens can't be forged; restarts sign everyone out) and logs `SECURITY:` at ERROR. It also logs ERROR for: a secret under 32 characters outside development, demo mode on outside development, and default demo passwords with demo mode off. | `test_default_auth_secret_is_replaced_outside_development`, `test_development_keeps_its_secret_and_production_flags_demo_mode` |
| F9 | Medium | Some badly formed but validly signed tokens caused a 500 or were accepted: `exp` missing (never expires), a string or object `exp` (500), a non-object payload (500), `iat` in the future, no `sub`, `alg` other than HS256. Non-ASCII tokens raised a non-ValueError. | `decode_token` checks shape, alg, types, `exp`, `iat` (60 s leeway) and `sub`. Every failure is a `ValueError`, so the API returns 401. | `test_malformed_signed_tokens_are_rejected_cleanly[8 cases]`, `test_garbage_tokens_are_401_not_500`, `test_non_ascii_or_non_string_tokens_raise_value_error`, `test_tampered_signature_is_rejected` |
| F10 | Medium | Unlimited password guessing on `/api/auth/login`. | After 5 failed attempts per (email, client IP) in 5 minutes, login returns 429 with `Retry-After`. The counter is in-process (demo-grade). Keying on email plus IP means a stranger can't lock a coordinator out from a different machine. | `test_repeated_failures_are_throttled_then_reset_by_success` |
| F11 | Low | Response timing showed which accounts exist: an unknown email returned at once, a known one ran PBKDF2. | Unknown emails are checked against a dummy hash, so both cases take the same time. | `test_unknown_email_costs_the_same_pbkdf2_work` |
| F12 | Low | `login` stripped the email, then passed the unstripped value to `authenticate`. A mixed-case `DEMO_*_EMAIL` was stored in that case but looked up lowercased, so it never matched. | The email is stripped and lowercased at login and when accounts are created. Lookup falls back to a case-insensitive exact regex for older records. | `test_login_normalises_email` |
| F13 | Low | `verify_password` used whatever iteration count the stored hash string named, so a corrupted hash could turn one login into minutes of CPU. Oversized passwords went straight into PBKDF2. | The iteration count must be between 100k and 2M. Email is capped at 254 characters and password at 1024 (422 otherwise). Hashes are compared as bytes. | `test_absurd_iteration_counts_are_refused`, `test_oversized_credentials_are_rejected_before_hashing` |
| F14 | Medium | Two sweeps could dispatch the same check-in: the background loop and `POST /api/admin/sweep`, or two workers. Both read `status: scheduled`. The second then found the first one's `intended` ledger row and marked it `unknown` while the call was still in progress. | Each sweep claims a check-in with an atomic `scheduled → dispatching` conditional update, and only the winner acts. A claim older than 10 minutes is treated as left by a crashed sweep and taken over; the ledger's "reused intent → unknown" path then stops a redial. | `test_claim_is_atomic`, `test_a_checkin_claimed_by_another_sweep_is_left_alone`, `test_stale_claim_after_a_crash_never_redials` |
| F15 | Medium | Every call error was recorded as `failed`, including a read timeout or a 5xx after ElevenLabs may already have placed the call. "Failed" says the call didn't happen, and the ledger lets a failed key be reused, so the patient could be called twice. | Connection refused or a 4xx is recorded as `failed`. A read timeout, dropped connection, 5xx or unreadable body is recorded as `unknown`, so a person reconciles it. | `test_ambiguous_call_outcomes_are_unknown_not_failed[4 cases]` |
| F16 | Low | A check-in for a patient who no longer exists stayed `scheduled` forever and was re-read on every sweep. | It is marked `needs_review`. | `test_checkin_for_missing_patient_is_flagged` |
| F18 | **High** (patient safety) | `_dialable` kept only the digits and added `+`, so a number saved without its country code (`416 555 0142`) was dialled as `+4165550142`, a different country. | North American 10-digit numbers get `DEFAULT_COUNTRY_CODE` (`1`) added; `+…` and `00…` numbers are kept as written; anything ambiguous (too short, a leading trunk `0`, more than 15 digits, a non-`1` default with no `+`) is not dialled and becomes a coordinator call. | `test_phone_numbers_are_normalised_or_refused[13 cases]`, `test_national_numbers_need_a_plus_outside_north_america`, `test_number_saved_without_country_code_dials_correctly` |
| F19 | Usability | A check-in held for calling hours gave no sign of why. | The sweep result lists it as `checkin_held` with `calls_after`, and includes `calling_hours` {window, open_now, next_opens}. The check-in gets a `dispatch_detail` explaining the wait. Holds aren't counted as actions in `scheduler_runs` (`held` instead). | `test_held_checkin_is_explained_in_the_sweep_and_on_the_checkin`, `test_next_window_before_opening_is_same_day` |
| F17 | Hardening | Getting ready for the SSE fix (A2): no way to issue a narrow-purpose token. | `create_token(..., scope=...)`. `require_auth` and `optional_user` reject any token that carries a `scope`, so a stream ticket that leaks from a log can't be used to call the API. | `test_scoped_tokens_are_not_sessions` |

Checked and found correct: roles are read from the database on every request,
never from the token (`test_role_comes_from_the_database_not_the_token`). The
signature compare is constant-time (`hmac.compare_digest`). `require_role`
returns 403 for a valid token with the wrong role and leaves 401 to `require_auth`.

### Behaviour changes to be aware of
- **Calling hours.** Tests that dispatch calls with `datetime.now()` depend on
  the time of day. The two in `test_resilience.py` now stub
  `within_calling_hours`; new tests pass a fixed 15:00 local time. The demo
  sweep (`/api/demo/sweep?days_ahead=N`) uses 23:59 UTC, which is 18:59 or
  19:59 in Toronto, so it stays inside the window.
- **New check-in statuses:** `dispatching` (claimed) and `awaiting_outreach`
  (a non-voice channel). Both count as "pending" for `_has_pending_checkin`.
- **Non-development deployments** need `CONTINUUM_VOICE_SECRET` set, or the
  voice API returns 503. They should also set `AUTH_SECRET`, or every restart
  signs users out.

### Frontend changes needed (coordinator-console session owns `frontend/`)
1. `frontend/src/pages/Login.tsx` already ignores a failed `api.auth.demo()`,
   so the page still loads when the endpoint returns 404. But the two demo
   buttons (around line 143) still render, and `loginDemo` in `auth.tsx:78`
   would throw. Render the demo stack only when `demoCreds` is set:
   `{demoCreds ? (<div className="stack auth-demo-stack">…</div>) : null}`.
2. `PatientJourney.tsx:263`: also highlight `checkin.status === "awaiting_outreach"`
   (and show `checkin.channel`) next to `awaiting_manual_call`.
3. For A2 below, move `EventSource` to a short-lived stream ticket.

---

## Part 2 — Audit findings (not fixed here; the patches are proposals)

Severity: **Critical** = exploitable now, or harms patients; **High** = likely
in production; **Medium** = needs a race, a misconfiguration or an insider; **Low** = hygiene.

### A1 — High: the FHIR client sends the EHR access token to any URL in the data
`backend/app/ingest/fhir_client.py:116-118` (`_url` returns absolute URLs
unchanged), used from `pull.py:198-200` (`client.get(attachment.url)`) and
`fhir_client.py:147` (the bundle `next` link).
**Scenario:** a DocumentReference whose `content.attachment.url` is
`https://attacker.example/x`, or `http://169.254.169.254/latest/meta-data/…`.
Continuum makes the request with `Authorization: Bearer <EHR system token>`.
That leaks a token with `system/*.read` on the hospital EHR, and gives
server-side request forgery into the internal network. Paginated `next` links
from a misconfigured proxy carry the same risk.
**Patch** (`fhir_client.py`):
```python
from urllib.parse import urlsplit

def _url(self, path_or_url: str) -> str:
    if path_or_url.startswith(("http://", "https://")):
        want, got = urlsplit(self.base_url), urlsplit(path_or_url)
        if (got.scheme, got.netloc) != (want.scheme, want.netloc) or not got.path.startswith(want.path):
            raise FHIRError(f"Refusing to follow a URL outside the FHIR base: {got.netloc}")
        return path_or_url
    return f"{self.base_url}/{path_or_url.lstrip('/')}"
```
Also set `httpx.Client(follow_redirects=False)` (the httpx default; keep it
that way). Test: an attachment pointing to another host raises `FHIRError`
and no request reaches that host (`httpx.MockTransport`).

### A2 — High: the SSE stream takes the long-lived session JWT in the query string
`backend/app/routes/live.py:21-29`.
**Scenario:** `GET /api/stream?token=eyJ…` ends up in uvicorn's access log
(on by default; `Dockerfile:35`), in any reverse-proxy or CDN log, and in
browser history. The token is a full coordinator session that lasts 10 hours
(`AUTH_TOKEN_TTL_MINUTES=600`). Anyone with log access can approve closures
as that coordinator. Separately, `decode_token` is called but the user is
never looked up, so a deleted account's token keeps streaming.
**Patch:** add a ticket endpoint and accept only tickets on the stream (F17
already makes tickets useless as sessions):
```python
@router.post("/stream/ticket")
def stream_ticket(user: dict = Depends(require_auth)) -> dict:
    return {"ticket": auth_service.create_token(sub=user["id"], email=user["email"], name=user["name"],
                                                role=user["role"], ttl_minutes=1, scope="stream")}

@router.get("/stream")
async def stream(request: Request, ticket: str = Query(...)):
    try:
        claims = auth_service.decode_token(ticket)
    except ValueError as exc:
        raise HTTPException(401, "Invalid ticket") from exc
    if claims.get("scope") != "stream" or not get_db().users.find_one({"id": claims["sub"]}):
        raise HTTPException(401, "Invalid ticket")
    ...
```
A 60-second ticket only has to be valid when the connection opens. Running
uvicorn with `--no-access-log`, or filtering `token=` out of the log, is a
good backstop.

### A3 — Medium: each SSE client occupies a worker thread, so about 40 open consoles stall the whole API
`backend/app/routes/live.py:37` (`await asyncio.to_thread(q.get, True, 15)`).
**Scenario:** `to_thread` uses anyio's default thread pool (40 tokens), the
same pool that runs every sync FastAPI route. Each open stream holds a thread
for up to 15 s, again and again. Forty console tabs, or one script opening 40
streams with a stolen token, leave every `def` endpoint (login, dashboard,
approvals) waiting for a thread. There's also no per-user cap on streams.
**Patch:** give each subscriber an `asyncio.Queue` and have the watcher
thread hand events over with `loop.call_soon_threadsafe(q.put_nowait, event)`.
Or poll without blocking: `q.get_nowait()` followed by `await asyncio.sleep(0.5)`.
Cap streams per user (for example 5).

### A4 — Medium: an HL7 message resent while the first copy is still processing is ingested twice
`backend/app/ingest/service.py:88-91` (dedupe reads `hl7_messages`, which is
written only at the end, at line 133), and `backend/app/ingest/jobs.py:62-84`
(`create_job` checks and then inserts; there is no unique index on
`ingest_jobs.key`).
**Scenario:** interface engines resend when no ACK arrives within their
timeout, often 30 s. A pull plus LLM narrative extraction takes longer, so
copy #2 arrives while #1 is still running. The dedupe misses it, and both
create a job: two job documents with the same key, both claimable. With the
episode race in A6 this can mean duplicate episodes, duplicate follow-ups,
and two check-in calls to the patient.
**Patch:** in `db.py`, `db.ingest_jobs.create_index("key", unique=True)` and
`db.actions.create_index("key", unique=True, partialFilterExpression={"key": {"$type": "string"}, "status": {"$ne": "failed"}})`.
In `create_job`, insert first and on `DuplicateKeyError` return the existing
job. In `handle_hl7`, record the message as received *before* the pull:
`update_one({control_id, sending_facility}, {"$setOnInsert": {..., "outcome": "processing"}}, upsert=True)`.
If `upserted_id is None` and the outcome is `processing`, reply
`AA "In progress"` and do nothing else.

### A5 — Medium: job leases are not owner-checked or renewed, so two workers can run one job
`backend/app/ingest/jobs.py:166-171, 190` (step updates filter on `{"id": job_id}` only), and `routes/ingest.py:113` (`force=true`).
**Scenario:** the `narrative` step calls the LLM and can take more than the
90 s `LEASE`. When the lease lapses, the next scheduler sweep
(`resume_pending`) or a second API instance claims the job and starts the
same step. Both workers then write `steps.*`, `state` and `status: done` over
each other, with no owner check. `force=true` on the resume endpoint (allowed
with only the shared integration token) takes a lease that is still live.
Builder steps are mostly idempotent, but narrative extraction checks and then
inserts, so AI-created follow-ups can be duplicated.
**Patch:** every job write uses `{"id": job_id, "lease_owner": WORKER}` and
stops the run when `matched_count == 0` (lease lost). Renew the lease
(heartbeat) from a timer while a step runs, or set `LEASE` above the LLM
timeout plus a margin. Let only a coordinator JWT use `force` (see A8).

### A6 — Medium: `step_identify` is not crash-safe; a restart can create a duplicate patient or episode
`backend/app/ingest/builder.py:287-308`: the patient or episode is created
(`intake_patient` / `_create_episode`), and the `ehr.encounter_ref` that later
re-runs look up is written afterwards in a separate `update_one` (line 308).
**Scenario:** the process dies between line 304 and line 308. The resumed job
doesn't find the episode by `encounter_ref`, falls through to `match_patient`
or creates a new patient, and makes a second patient or episode with the same
follow-ups. The coordinator sees two Michael Chens, and both get check-ins.
**Patch:** pass the `ehr` block into `_create_episode` and `intake_patient`
so it is written in the same insert, or save `state.episode_id` on the job
before creating, and look it up first on re-run.

### A7 — High (patient safety): EHR-ingested patients get automated calls by default, with consent assumed
`backend/app/schemas.py:182` (`consent_to_automated_calls: bool = True`), used by `builder.py:292-304`.
**Scenario:** a discharge pulled from the EHR goes through `PatientIntakeRequest`
without the consent field, so the stored record says the patient consented.
F4 only covers records where the flag is *missing*. These have an explicit
`True` that nobody actually gave. With telephony configured, the next sweep
robo-calls a patient who never agreed to it.
**Patch:** in `builder.step_identify`, pass `consent_to_automated_calls=False`,
`consent_to_recording=False` (and `channel` from `Patient.communication` /
`telecom` when present). Change the schema default to `False`, and have the
coordinator intake form ask explicitly. Seeded demo patients already set the flag.

### A8 — Medium: the shared integration token can read everything and steal ingestion leases
`backend/app/routes/ingest.py:76-118`, `routes/integrations.py:16-26`.
**Scenario:** `INTEGRATION_SECRET` is one secret shared by every clinic and
lab that posts records. Whoever holds it can also call `GET /ingest/status`
(the last 10 HL7 messages, with patient MRNs), `GET /ingest/jobs` /
`/jobs/{id}` (job state, including `patient_id`), and
`POST /jobs/{id}/resume?force=true` (A5). A write-only credential turns
into read access to PHI across clinics.
**Patch:** split the auth helper: `_authorize_push` (token or coordinator)
for `/ingest/hl7`, `/ingest/pull`, `/integrations/fhir`, and
`require_role("coordinator")` for status, jobs, resume and poll. In the
longer term, give each clinic its own token, scoped to its facility.

### A9 — High when exposed: the MLLP listener has no auth, no timeouts and no connection cap
`backend/app/ingest/mllp.py:19-39`.
**Scenario:** with `HL7_MLLP_HOST=0.0.0.0` (a natural thing to set in Docker,
where binding 127.0.0.1 makes the listener unreachable), anyone who can reach
the port can send `ADT^A03` messages. Each one triggers an EHR pull and
patient creation, then automated check-in calls (made worse by A7). A client
that never sends `\x1c\x0d` holds its connection forever (slowloris). A
message over 64 KiB raises `LimitOverrunError`, which isn't caught, so the
connection dies without an ACK and the engine resends it again and again.
**Patch:**
```python
ALLOWED = {ip.strip() for ip in settings.hl7_mllp_allowed_peers.split(",") if ip.strip()}  # new setting
async def _handle(reader, writer):
    peer = (writer.get_extra_info("peername") or ("?",))[0]
    if ALLOWED and peer not in ALLOWED:
        writer.close(); return
    try:
        while True:
            data = await asyncio.wait_for(reader.readuntil(MLLP_END.encode()), timeout=60)
            ...
    except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.TimeoutError, asyncio.LimitOverrunError):
        pass
```
Use `asyncio.start_server(..., limit=1_048_576)`. Refuse a non-loopback
`HL7_MLLP_HOST` unless `HL7_MLLP_ALLOWED_PEERS` is set, and log the refusal at ERROR.

### A10 — Medium (wrong patient): patient matching ignores the identifier system and the FHIR server
- `backend/app/ingest/pull.py:133, 147`: `search("Patient", {"identifier": value})` sends only the value.
- `backend/app/integrations.py:97`: `Patient/<ref>` is matched against `id` **or** `mrn` **or** `ehr.fhir_id` across every FHIR base.
**Scenario:** an ADT names MRN `12345` from facility A. The EHR also holds a
patient with insurance member number `12345` in a different system, and that
is the only hit, so the code takes it as the match. Continuum then tracks
and phones the wrong person about someone else's follow-ups. On the push
side, `Patient/123` from clinic B's FHIR server matches a patient whose MRN
is `123` at the hospital, and their evidence (and verification approvals)
gets attached to the wrong record.
**Patch:** search with `system|value` (map the PID-3 assigning authority to
a configured system URI), and after a hit, compare the birth date and family
name from PID-7 and PID-5 before accepting it. In `resolve_patient`, match
`ehr.fhir_id` together with `ehr.fhir_base` (pass the sender's base) and stop
using MRN as a fallback for a FHIR reference.

### A11 — Medium: ledger state changes have no from-state guard
`backend/app/ledger.py:83-87` (`_move` updates by `id` only).
**Scenario:** the scheduler records intent and starts the call. Meanwhile a
demo sweep with a clock 30 days ahead (`/api/demo/sweep?days_ahead=30`) runs
`reconcile`, sees the intent as stale and marks it `unknown`. Then the first
sweep's `mark_sent` overwrites `unknown` with `sent`, so the warning to the
coordinator disappears. In the other direction, `confirm()` accepts
`intended → confirmed` (line 103), which skips `sent`: a forged or replayed
post-call webhook with a guessed conversation id can confirm an action that
was never sent (mitigated by F6/F7 outside development).
**Patch:**
```python
ALLOWED = {SENT: {INTENDED}, FAILED: {INTENDED, SENT, UNKNOWN}, UNKNOWN: {INTENDED},
           UNCONFIRMED: {SENT}, CONFIRMED: {SENT, UNCONFIRMED, UNKNOWN}}
def _move(db, action_id, status, detail, **fields):
    res = db.actions.update_one({"id": action_id, "status": {"$in": list(ALLOWED[status])}}, update)
    if res.matched_count == 0:
        log.warning("Ledger: refused %s → %s for %s", "?", status, action_id)
    return db.actions.find_one({"id": action_id}, {"_id": 0})
```
Also run `reconcile` only with the real wall clock, never the demo-shifted one.
**Seen live:** in a demo run, `POST /api/demo/sweep?days_ahead=45` twice in a
row. The second sweep reported `action_unconfirmed: 8`. The 8 coordinator
tasks the first sweep had just queued (with `confirm_by` = real now + 72 h)
were judged against a clock 45 days ahead and flagged "Action outcome
unverified" within seconds of being created. The fix is to pass
`datetime.now(timezone.utc)` to `ledger.reconcile` in `scheduler.run_sweep`
and `routes/meta.demo_sweep`, or store `confirm_by` on the same demo clock.

### A12 — Low/Medium: clinic resources without an `id` defeat idempotency; the evidence dedupe can race
`backend/app/integrations.py:183, 193`.
**Scenario:** a lab resends an `Appointment` that has no `id`. Each copy gets
a fresh random id (`new_id("fhir")`), so it isn't seen as a duplicate:
duplicate evidence, duplicate events, and a second verification approval.
Two copies arriving at the same moment both pass the `find_one` check at
line 193 (the `evidence.source_ref` index isn't unique).
**Patch:** reject resources with no `id` (422 for that entry), or derive the
id from `sha256(canonical_json(resource))`. Make the `evidence.source_ref`
index unique and partial (`$type: "string"`), and treat `DuplicateKeyError`
as `duplicate`.

### A13 — Low: order text in the logs
`backend/app/ingest/orders.py:166` logs the free text of a clinical order at
INFO. Order text is PHI (§2 of `docs/compliance.md`).
**Patch:** log the order id and score only: `log.info("Closest past order for %s scored %.2f …", order_ref, score)`.
Other logging checked and acceptable: MLLP logs control id, peer and outcome.
Jobs and `service.py` log exception text, which comes from `PullError`
("Patient not found", or `Patient/<fhir id>`) and `FHIRError` (status code
plus up to 300 characters of the EHR's error body). That body *could* repeat
search parameters such as an MRN. **Patch:** keep `response.text` out of the
logs, store it on the job only, and log just the status code.

### A14 — Low: the change-stream summary is fine; `/api/actions` has no upper limit
`streams.summarize` sends only ids, status, step and changed *field names*.
No names, descriptions or transcripts go over SSE. `streams.WATCHED`
includes `patients` and `events`, but only ids leave the server. Good.
`routes/live.py:54`: `limit` has no upper bound (`?limit=100000000`).
Clamp it: `limit = max(1, min(limit, 500))`.

### A15 — Info: action-key idempotency depends on a non-unique index
`ledger.intend` (`ledger.py:59`) checks and then inserts on `key`, and
`db.actions` has no unique index. For scheduler calls, F14's atomic claim now
blocks the duplicate. Other callers of `intend` (coordinator tasks keyed by
approval id) are protected by approval dedupe. The unique partial index in
A4 closes the gap for good.

---

## Live check (uvicorn on :8012, Atlas Local)

**Production-like** (`ENVIRONMENT=production`, `DEMO_MODE=false`, no
`AUTH_SECRET`, no voice secret):
- Startup logged `SECURITY:` errors for the default secret and the
  default-password demo accounts; no demo users were created.
- `GET /api/auth/demo` → 404.
- Login with the default demo password → 401.
- `/api/voice/identify` → 503.
- Unauthenticated `/api/voice/calls/…/sync` → 401.
- A token signed with the public default secret → 401.

**Demo** (`ENVIRONMENT=development`, `DEMO_MODE=true`, telephony off):
- `/api/auth/demo` → 200.
- Login with `"  COORDINATOR@continuum.demo "` → 200.
- The 6th wrong password in a row → 429.
- Seed, Day 0, then two sweeps 45 days ahead:
  - Sarah Okafor (Text): 2 check-ins `awaiting_outreach`, "Contact Sarah Okafor by Text…" tasks, 0 call attempts.
  - Michael Chen and David Morin (Voice call): `awaiting_manual_call` with "Call …" tasks.
- Calls through a real telephony account were not exercised.

## Not verified / out of scope
- Not tested against a real ElevenLabs account. The timeout/5xx handling
  (F15) is tested with mocked `httpx` only.
- The login throttle (F10) lives in one process. With several workers, each
  has its own counter. Real deployments should rate-limit at the gateway or IdP.
- `logout` doesn't revoke tokens; that is unchanged and documented.
  Revocation would need a `jti` denylist or a short TTL with refresh tokens.
- The frontend wasn't changed or exercised in a browser; see "Frontend changes needed".
