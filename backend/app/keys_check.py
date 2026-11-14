"""Check every external provider in one go.

    cd backend && .venv/bin/python -m app.keys_check          # make tiny real calls where a key is set
    cd backend && .venv/bin/python -m app.keys_check --offline  # configuration only, no network

For each provider: is it configured, does it actually answer, and what it
unlocks in Continuum. Exit code 0 when everything that is configured works.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

OK, FAIL, OFF = "✓", "✗", "–"
results: list[tuple[str, str, str, str]] = []


def report(name: str, mark: str, detail: str, unlocks: str) -> None:
    results.append((name, mark, detail, unlocks))
    print(f"  {mark} {name:<22} {detail}")
    if mark != OK:
        print(f"    {'':<22} unlocks: {unlocks}")


def _timed(fn):
    start = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - start) * 1000


def check_llm(offline: bool) -> None:
    from .agent.llm import chat_configured, chat_model_name, chat_provider, get_chat_model, provider_credentials
    from .config import settings

    provider, model = chat_provider(), chat_model_name()
    unlocks = "real extraction of discharge summaries and transcripts, agent replies (else deterministic rules)"
    configured, how = provider_credentials(provider, settings.llm_api_key)
    if not configured:
        report("LLM", OFF, f"{provider}:{model} not configured ({how})", unlocks)
        return
    chat = get_chat_model()
    if chat is None:
        report("LLM", FAIL, f"{provider}:{model} configured but could not be created (missing package? see logs)", unlocks)
        return
    if offline:
        report("LLM", OK, f"{provider}:{model} ({how}), not called (--offline)", unlocks)
        return
    try:
        reply, ms = _timed(lambda: chat.invoke("Reply with exactly: OK"))
        from .agent.llm import message_text

        text = message_text(reply)
        report("LLM", OK, f"{provider}:{model} answered {text.strip()[:20]!r} in {ms:.0f} ms", unlocks)
    except Exception as exc:
        report("LLM", FAIL, f"{provider}:{model} call failed: {str(exc)[:160]}", unlocks)
    _ = chat_configured


def check_embeddings(offline: bool) -> None:
    from .agent.llm import embedding_label, embedding_provider_name, get_embeddings
    from .config import settings

    unlocks = "semantic memory recall + Vector Search order matching with a hosted model (local model works without a key)"
    if offline and settings.embedding_provider not in {"local", "none"}:
        report("Embeddings", OK, f"would try {embedding_provider_name()} then fall back to local (--offline)", unlocks)
        return
    model, ms = _timed(get_embeddings)
    if model is None:
        report("Embeddings", OFF, "disabled (EMBEDDING_PROVIDER=none): lexical recall only", unlocks)
        return
    label = embedding_label()
    mark = OK if not label.startswith("local:") or settings.embedding_provider in {"auto", "local"} else FAIL
    report("Embeddings", mark, f"{label}, {getattr(model, 'dimensions', '?')} dims (picked in {ms:.0f} ms)", unlocks)


def check_langsmith(offline: bool) -> None:
    from .config import settings

    unlocks = "a trace for every agent run and ingestion job, LangSmith eval experiments"
    if not settings.tracing_key:
        report("LangSmith", OFF, "no LANGSMITH_API_KEY / LANGCHAIN_API_KEY", unlocks)
        return
    if not settings.tracing_enabled:
        report("LangSmith", OFF, "key set but tracing switched off (LANGSMITH_TRACING=false)", unlocks)
        return
    if offline:
        report("LangSmith", OK, f"project {settings.tracing_project}, not called (--offline)", unlocks)
        return
    try:
        from langsmith import Client

        client = Client(api_key=settings.tracing_key)
        _, ms = _timed(lambda: list(client.list_projects(limit=1)))
        report("LangSmith", OK, f"key valid, traces go to project {settings.tracing_project} ({ms:.0f} ms)", unlocks)
    except Exception as exc:
        report("LangSmith", FAIL, f"key rejected or unreachable: {str(exc)[:160]}", unlocks)


def _eleven(path: str, key: str) -> httpx.Response:
    return httpx.get(f"https://api.elevenlabs.io{path}", headers={"xi-api-key": key}, timeout=15)


def check_elevenlabs(offline: bool) -> None:
    from .config import settings

    unlocks = "live voice: patient check-in calls, the in-console call panel, post-call transcripts as evidence"
    key = settings.elevenlabs_api_key
    if not key:
        report("ElevenLabs API", OFF, "no ELEVENLABS_API_KEY", unlocks)
    elif offline:
        report("ElevenLabs API", OK, "key set, not called (--offline)", unlocks)
    else:
        try:
            r = _eleven("/v1/user", key)
            report("ElevenLabs API", OK if r.status_code == 200 else FAIL,
                   "key valid" if r.status_code == 200 else f"HTTP {r.status_code}: {r.text[:120]}", unlocks)
        except Exception as exc:
            report("ElevenLabs API", FAIL, f"unreachable: {exc}", unlocks)
    agent = settings.elevenlabs_agent_id
    if not agent:
        report("ElevenLabs agent", OFF, "no ELEVENLABS_AGENT_ID (create one: docs/elevenlabs-setup.md)", "the Continuum voice agent (browser + phone)")
    elif key and not offline:
        try:
            r = _eleven(f"/v1/convai/agents/{agent}", key)
            name = r.json().get("name") if r.status_code == 200 else None
            report("ElevenLabs agent", OK if r.status_code == 200 else FAIL,
                   f"agent '{name}'" if name else f"HTTP {r.status_code}", "the Continuum voice agent (browser + phone)")
        except Exception as exc:
            report("ElevenLabs agent", FAIL, f"unreachable: {exc}", "the Continuum voice agent")
    else:
        report("ElevenLabs agent", OK, f"id {agent[:10]}…", "the Continuum voice agent (browser + phone)")
    phone = settings.elevenlabs_phone_number_id
    report("Phone number", OK if phone else OFF, f"id {phone[:10]}…" if phone else "no ELEVENLABS_PHONE_NUMBER_ID (browser calls still work)",
           "real outbound phone calls to patients")
    report("Voice tool secret", OK if settings.continuum_voice_secret else OFF,
           "set" if settings.continuum_voice_secret else "CONTINUUM_VOICE_SECRET empty: voice tool endpoints are unauthenticated",
           "safe voice tool calls from ElevenLabs into Continuum")


def check_atlas(offline: bool) -> None:
    from .config import settings

    unlocks = "the real database: Vector Search, change streams, schema validation (required for the hackathon)"
    if settings.is_memory_db:
        report("MongoDB", OFF, "CONTINUUM_DB_MODE=memory (mongomock)", unlocks)
        return
    if offline:
        report("MongoDB", OK, "configured, not called (--offline)", unlocks)
        return
    from .atlas_setup import diagnose
    from .db import get_client, vector_search_available

    try:
        client = get_client()
        _, ms = _timed(lambda: client.admin.command("ping"))
        kind = "Atlas" if "mongodb.net" in (settings.mongodb_uri or "") else "self-hosted / Atlas Local"
        report("MongoDB", OK, f"{kind} reachable ({ms:.0f} ms), vector index queryable: {vector_search_available()}", unlocks)
    except Exception as exc:
        report("MongoDB", FAIL, diagnose(exc, settings.mongodb_uri or ""), unlocks)


def check_ehr(offline: bool) -> None:
    from .config import settings

    unlocks = "automatic discharges over HL7/FHIR and the result poller"
    if not settings.ehr_fhir_base_url:
        report("EHR (FHIR)", OFF, "no EHR_FHIR_BASE_URL", unlocks)
        return
    if offline:
        report("EHR (FHIR)", OK, f"{settings.ehr_fhir_base_url}, not called (--offline)", unlocks)
        return
    try:
        r = httpx.get(f"{settings.ehr_fhir_base_url.rstrip('/')}/metadata", headers={"Accept": "application/fhir+json"}, timeout=15)
        ok = r.status_code == 200
        report("EHR (FHIR)", OK if ok else FAIL,
               f"{r.json().get('software', {}).get('name', 'FHIR server')} {r.json().get('fhirVersion', '')} at {settings.ehr_fhir_base_url}" if ok else f"HTTP {r.status_code}",
               unlocks)
    except Exception as exc:
        report("EHR (FHIR)", FAIL, f"unreachable: {exc}", unlocks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="Only check configuration; make no network calls")
    args = parser.parse_args()
    print("\nContinuum provider check\n")
    for check in (check_llm, check_embeddings, check_langsmith, check_elevenlabs, check_atlas, check_ehr):
        try:
            check(args.offline)
        except Exception as exc:  # never let one provider hide the others
            report(check.__name__.replace("check_", ""), FAIL, f"check crashed: {exc}", "")
    failed = [r for r in results if r[1] == FAIL]
    off = [r for r in results if r[1] == OFF]
    print(f"\n{len(results) - len(failed) - len(off)} working · {len(off)} not configured · {len(failed)} failing")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
