"""Test bootstrap: force deterministic, in-memory behaviour."""

from __future__ import annotations

import os
import uuid

os.environ["CONTINUUM_DB_MODE"] = "memory"
os.environ["MONGODB_DB"] = f"continuum_test_{uuid.uuid4().hex[:6]}"
os.environ["AUTO_SEED"] = "true"
os.environ["DEMO_MODE"] = "true"
os.environ["LLM_API_KEY"] = ""
# Lexical recall in tests: no model download, fully deterministic.
os.environ["EMBEDDING_PROVIDER"] = "none"
os.environ["OPENAI_API_KEY"] = ""
os.environ["LANGCHAIN_TRACING_V2"] = "false"
# Real .env may set a live voice secret; disable token enforcement so the
# voice endpoints are reachable without a token in tests (it is opt-in).
os.environ["CONTINUUM_VOICE_SECRET"] = ""
os.environ["ELEVENLABS_WEBHOOK_SECRET"] = ""
# The sweep is exercised explicitly in tests, never on a background timer.
os.environ["SCHEDULER_INTERVAL_SECONDS"] = "0"
os.environ["INTEGRATION_SECRET"] = "test-integration-secret"
os.environ["ELEVENLABS_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from app.agent.checkpoint import get_checkpointer
from app.db import get_db
from app.main import app
from app.seed import seed_all

PATIENTS = ["pat_michael", "pat_sarah", "pat_david", "pat_amina"]


@pytest.fixture()
def client():
    seed_all(reset=True)
    for patient_id in PATIENTS:
        try:
            get_checkpointer().delete_thread(patient_id)
        except Exception:
            pass
    with TestClient(app) as test_client:
        # Sign in the synthetic coordinator so data endpoints resolve auth.
        credentials = test_client.get("/api/auth/demo").json()["coordinator"]
        login = test_client.post(
            "/api/auth/login",
            json={"email": credentials["email"], "password": credentials["password"]},
        )
        assert login.status_code == 200, login.text
        test_client.headers["Authorization"] = f"Bearer {login.json()['token']}"
        yield test_client


@pytest.fixture()
def db():
    return get_db()
