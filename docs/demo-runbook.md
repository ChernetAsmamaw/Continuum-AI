# Demo runbook

A one-page script for the hackathon demo: what to set up before, what to click
and say during, and what to do when something breaks.

## The night before

1. **Pull main and run the tests.** `cd backend && .venv/bin/python -m pytest -q` (all green).
2. **Fill `.env`:** `MONGODB_URI` with `CONTINUUM_DB_MODE=atlas`, `LLM_API_KEY`
   (plus `LLM_BASE_URL`/`LLM_MODEL` if not OpenAI), the ElevenLabs keys, and
   `LANGCHAIN_TRACING_V2=true` with `LANGCHAIN_API_KEY`.
3. **Check the vector index.** Start the API and run `curl -s localhost:8000/api/health`.
   You want `"vector_search": true`. If it's `false`, create the index from
   [atlas.md](atlas.md) section 3 and wait for **Ready**.
4. **Run the evaluation into LangSmith.**
   `cd backend && .venv/bin/python -m evals.run --langsmith`. Open the
   `continuum-care-loops` dataset, select both experiments and keep that
   comparison tab open. With your LLM key set, the numbers come from the real
   model; put them in your slides in place of the offline ones below if they differ.
5. **Voice.** Start the tunnel, then point the agent at it:
   `.venv/bin/python ../scripts/point_voice_agent.py https://<tunnel>.trycloudflare.com`
   and restart the backend. Make one test call from the browser widget.
6. **Phone (optional).** Import a Twilio number in ElevenLabs (Agents → Phone
   numbers) and set `ELEVENLABS_PHONE_NUMBER_ID`. Without it, scheduled
   check-ins become "call the patient" tasks and the browser widget is the live channel.

## On the day (about 5 minutes)

Open the console signed in as the coordinator. Reset with **Demo runner → Day 0**.

| Step | Click | Say |
| --- | --- | --- |
| Day 0 | Demo runner → Day 0, then open Michael Chen's journey | "A discharge summary becomes four tracked follow-ups, each linked to the sentence it came from." |
| Day 3 | Day 3, expand the call transcript | "Michael says he saw his doctor. Continuum records it, but a patient's word isn't proof, so it stays open and asks the clinic to confirm." |
| Day 7 | Day 7 | "The cardiology clinic confirms a date. That's evidence, so the status changes." |
| Day 10 | Day 10, then Approvals | "Michael gives a date three days later. A system without memory would just believe him. Continuum remembers the clinic's date, keeps both, and asks a coordinator." |
| Day 12 | Day 12, then the Inspector | "A dose question. The agent declines and routes it to a clinician. Here's the LangGraph checkpoint and the reasoning behind every step." |
| Live | The voice call widget | Ask "When is my cardiology appointment?" live. |
| Proof | LangSmith comparison tab | The numbers below. |

## Numbers to quote

From `python -m evals.run` (21 synthetic cases; Continuum vs. the same
extractor with no memory and no loop engine):

- **100% vs 0%** of date contradictions caught and preserved.
- **100% vs 15%** of follow-ups in the right state after several conversations.
- **100% vs 2%** of open follow-ups still tracked after later calls.
- **0** patient self-reports treated as proof (the baseline closed 40% of them).
- **100%** extraction recall on six discharge summaries, the same as the
  baseline. Extraction isn't the hard part; remembering is.

## If something breaks

| Problem | Fallback |
| --- | --- |
| Atlas unreachable | `CONTINUUM_DB_MODE=memory`, restart. The whole demo runs in memory. |
| LLM slow or down | Unset `LLM_API_KEY`. The deterministic extractor runs every day of the demo. |
| Tunnel or voice fails | Skip the live call. Day 3, 10 and 12 already show call transcripts. |
| A day runs out of order | Demo runner → Day 0 resets Michael, then replay. |
| Thread stuck "awaiting approval" | Resolve it in Approvals, or replay Day 0. |
