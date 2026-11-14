"""Create the Continuum voice agent on ElevenLabs (tools included).

    backend/.venv/bin/python scripts/create_voice_agent.py https://<public-url>

Creates the seven webhook tools pointing at <public-url>/api/voice/*, each
sending the x-continuum-token header, creates the agent with the prompt in
scripts/voice_agent_prompt.txt, attaches the tools and writes
ELEVENLABS_AGENT_ID to .env. Run scripts/point_voice_agent.py afterwards for
the post-call webhook (and whenever the public URL changes).
"""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
PROMPT = Path(__file__).with_name("voice_agent_prompt.txt")
API = "https://api.elevenlabs.io"

FIRST_MESSAGE = (
    "Hi, this is Continuum, an AI care assistant calling on behalf of your care team. "
    "This call is recorded and transcribed. Is it okay to continue?"
)

S = lambda d: {"type": "string", "description": d}  # noqa: E731
CALL_SESSION = {"type": "string", "dynamic_variable": "call_session_id"}

TOOLS = [
    {
        "name": "identify_patient", "path": "/api/voice/identify", "method": "POST",
        "description": "Look up the caller in the clinic roster. Call with the caller's name and date of birth "
                       "(YYYY-MM-DD). Follow the guidance it returns; do not discuss care until identity is verified.",
        "body": {"given_name": S("Caller's first name"), "family_name": S("Caller's last name"),
                 "birth_date": S("Date of birth as YYYY-MM-DD"), "phone": S("Phone number, only if asked for it")},
        "required": ["given_name"],
    },
    {
        "name": "get_patient_context", "path": "/api/voice/context/{patient_id}", "method": "GET",
        "description": "Get the verified patient's open follow-ups and preferences. Call once, right after "
                       "identity is verified.",
        "path_params": {"patient_id": S("patient_id returned by identify_patient")},
    },
    {
        "name": "submit_patient_report", "path": "/api/voice/report", "method": "POST",
        "description": "Record what the patient said about a follow-up (done, scheduled, not done, blocked). "
                       "Patient reports are unverified: tell the patient it was noted for the care team, never "
                       "that it is confirmed.",
        "body": {"patient_id": S("Verified patient_id"), "report": S("The patient's words, as close to verbatim as possible"),
                 "obligation_id": S("Follow-up id from get_patient_context, if it is about one"),
                 "reported_status": S("completed, scheduled, not_done or blocked"),
                 "reported_date": S("Date the patient mentioned, as YYYY-MM-DD")},
        "required": ["patient_id", "report"],
    },
    {
        "name": "escalate_to_clinician", "path": "/api/voice/escalate", "method": "POST",
        "description": "Hand a clinical question (medication, symptoms, treatment) or an emergency to a clinician. "
                       "Never answer clinical questions yourself.",
        "body": {"patient_id": S("Verified patient_id"), "reason": S("The question or concern in the patient's words"),
                 "urgency": {"type": "string", "description": "routine, urgent or emergency",
                             "enum": ["routine", "urgent", "emergency"]}},
        "required": ["patient_id", "reason"],
    },
    {
        "name": "book_appointment", "path": "/api/voice/appointment", "method": "POST",
        "description": "Request to book an appointment for the verified patient. The clinic confirms the time "
                       "afterwards. After calling this, tell the patient the request was sent and the clinic will "
                       "confirm. NEVER say the appointment is booked.",
        "body": {"patient_id": S("Verified patient_id"), "requested_time": S("The time the patient asked for, in their words"),
                 "obligation_id": S("Follow-up id this booking is for, if known"), "notes": S("Anything else the patient said")},
        "required": ["patient_id", "requested_time"],
    },
    {
        "name": "send_follow_up", "path": "/api/voice/follow-up", "method": "POST",
        "description": "Ask the care team to send the patient something they requested. After calling this, tell "
                       "the patient it has been requested; never say it has already been sent.",
        "body": {"patient_id": S("Verified patient_id"), "item": S("What to send, e.g. medication list"),
                 "reason": S("Why the patient wants it")},
        "required": ["patient_id", "item"],
    },
    {
        "name": "request_callback", "path": "/api/voice/checkin", "method": "POST",
        "description": "Schedule a callback: the care team will call the patient at a time they choose. This is "
                       "confirmed immediately on the call.",
        "body": {"patient_id": S("Verified patient_id"), "when": S("When to call back, in the patient's words"),
                 "reason": S("What the callback is about")},
        "required": ["patient_id", "when"],
    },
]


def load_env() -> dict[str, str]:
    out = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main(base: str) -> None:
    base = base.rstrip("/")
    env = load_env()
    key, token = env["ELEVENLABS_API_KEY"], env.get("CONTINUUM_VOICE_SECRET", "")
    if not token:
        sys.exit("Set CONTINUUM_VOICE_SECRET in .env first.")

    def call(method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            API + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"xi-api-key": key, "content-type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as exc:
            sys.exit(f"{method} {path} failed: {exc.code} {exc.read().decode()[:600]}")

    tool_ids = []
    for t in TOOLS:
        schema = {"url": base + t["path"], "method": t["method"], "request_headers": {"x-continuum-token": token}}
        if t.get("path_params"):
            schema["path_params_schema"] = t["path_params"]
        if t.get("body"):
            props = dict(t["body"])
            props["call_session_id"] = CALL_SESSION
            schema["request_body_schema"] = {"type": "object", "description": t["description"],
                                             "properties": props, "required": t.get("required", [])}
        cfg = {"type": "webhook", "name": t["name"], "description": t["description"], "api_schema": schema}
        created = call("POST", "/v1/convai/tools", {"tool_config": cfg})
        tool_ids.append(created["id"])
        print(f"tool {t['name']}: {schema['url']}")

    agent = call("POST", "/v1/convai/agents/create", {
        "name": "Continuum",
        "conversation_config": {
            "agent": {
                "first_message": FIRST_MESSAGE,
                "language": "en",
                "dynamic_variables": {"dynamic_variable_placeholders": {"call_session_id": ""}},
                "prompt": {"prompt": PROMPT.read_text().strip(), "tool_ids": tool_ids},
            },
        },
    })
    agent_id = agent["agent_id"]
    text = ENV.read_text()
    line = f"ELEVENLABS_AGENT_ID={agent_id}"
    text = re.sub(r"^ELEVENLABS_AGENT_ID=.*$", line, text, flags=re.M) if "ELEVENLABS_AGENT_ID=" in text else text + "\n" + line + "\n"
    ENV.write_text(text)
    print(f"agent Continuum: {agent_id} (written to .env)")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("usage: create_voice_agent.py https://<public-url>")
    main(sys.argv[1])
