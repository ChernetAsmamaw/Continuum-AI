"""Point the ElevenLabs agent's tools and post-call webhook at a new public URL.

Quick tunnels (cloudflared --url) get a new address on every restart. Run:

    backend/.venv/bin/python scripts/point_voice_agent.py https://<new>.trycloudflare.com

It rewrites every webhook tool on ELEVENLABS_AGENT_ID to the new host (and makes
sure each POST tool carries the call_session_id / conversation_id linkage params
and the agent has its call_session_id placeholder), creates a post-call webhook
on the new host (HMAC, with the x-continuum-token header), wires it into the
ConvAI workspace settings AND the agent's own post-call override (an agent whose
override is null was seen not to deliver transcripts), disables the previous
one, and writes the new ELEVENLABS_WEBHOOK_SECRET into .env. Restart the backend
afterwards.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from create_voice_agent import DYNAMIC_VARIABLES, LINKAGE

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
API = "https://api.elevenlabs.io"


def load_env() -> dict[str, str]:
    out = {}
    for line in ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def ensure_linkage(cfg: dict) -> None:
    """Add the call_session_id / conversation_id body params to a POST tool config."""
    body = (cfg.get("api_schema") or {}).get("request_body_schema")
    if body is not None:
        body.setdefault("properties", {}).update(LINKAGE)


def sync_agent(call, agent_id: str, webhook_id: str | None = None, prompt: str | None = None) -> None:
    """Placeholder for call_session_id, optional prompt, and the agent-level post-call webhook."""
    agent_cfg: dict = {"dynamic_variables": DYNAMIC_VARIABLES}
    if prompt is not None:
        agent_cfg["prompt"] = {"prompt": prompt}
    body: dict = {"conversation_config": {"agent": agent_cfg}}
    if webhook_id:
        body["platform_settings"] = {"workspace_overrides": {"webhooks": {
            "post_call_webhook_id": webhook_id, "events": ["transcript"], "transcript_format": "json"}}}
    call("PATCH", f"/v1/convai/agents/{agent_id}", body)


def main(new_base: str) -> None:
    new_base = new_base.rstrip("/")
    env = load_env()
    key, agent_id, token = env["ELEVENLABS_API_KEY"], env["ELEVENLABS_AGENT_ID"], env.get("CONTINUUM_VOICE_SECRET", "")

    def call(method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            API + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"xi-api-key": key, "content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    agent = call("GET", f"/v1/convai/agents/{agent_id}")
    for tid in agent["conversation_config"]["agent"]["prompt"].get("tool_ids") or []:
        cfg = call("GET", f"/v1/convai/tools/{tid}")["tool_config"]
        url = (cfg.get("api_schema") or {}).get("url")
        if not url or "/api/" not in url:
            continue
        parts = urlsplit(url)
        cfg["api_schema"]["url"] = new_base + url[len(f"{parts.scheme}://{parts.netloc}"):]
        ensure_linkage(cfg)
        call("PATCH", f"/v1/convai/tools/{tid}", {"tool_config": cfg})
        print(f"tool {cfg['name']}: {cfg['api_schema']['url']}")

    old_id = (call("GET", "/v1/convai/settings").get("webhooks") or {}).get("post_call_webhook_id")
    created = call(
        "POST",
        "/v1/workspace/webhooks",
        {
            "settings": {
                "auth_type": "hmac",
                "name": f"Continuum post-call ({urlsplit(new_base).netloc})",
                "webhook_url": f"{new_base}/api/voice/post-call",
                "request_headers": {"x-continuum-token": token},
            }
        },
    )
    call(
        "PATCH",
        "/v1/convai/settings",
        {"webhooks": {"post_call_webhook_id": created["webhook_id"], "events": ["transcript"], "transcript_format": "json"}},
    )
    sync_agent(call, agent_id, webhook_id=created["webhook_id"])
    if old_id and old_id != created["webhook_id"]:
        call("PATCH", f"/v1/workspace/webhooks/{old_id}", {"name": "Continuum post-call (retired)", "is_disabled": True})
    text = ENV.read_text()
    line = f"ELEVENLABS_WEBHOOK_SECRET={created['webhook_secret']}"
    text = re.sub(r"^ELEVENLABS_WEBHOOK_SECRET=.*$", line, text, flags=re.M) if "ELEVENLABS_WEBHOOK_SECRET=" in text else text + "\n" + line + "\n"
    ENV.write_text(text)
    print(f"post-call webhook: {new_base}/api/voice/post-call (secret written to .env)")
    print("Restart the backend so it picks up the new webhook secret.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        sys.exit("usage: point_voice_agent.py https://<public-url>")
    main(sys.argv[1])
