"""Safety regressions: demo credentials, token handling, login, voice auth, and
the scheduler's respect for contact preferences, calling hours and concurrency."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import mongomock
import pytest
from fastapi.testclient import TestClient

from app import auth, ledger, scheduler
from app import repository as repo
from app.config import DEFAULT_AUTH_SECRET, Settings, harden, settings
from app.main import app
from app.scheduler import run_sweep

MICHAEL = "pat_michael"


@pytest.fixture(autouse=True)
def _fresh_throttle():
    auth.login_throttle.reset()
    yield
    auth.login_throttle.reset()


def _at(hour: int, d: date | None = None) -> datetime:
    d = d or date.today()
    return datetime(d.year, d.month, d.day, hour, tzinfo=ZoneInfo(settings.call_timezone)).astimezone(timezone.utc)


def _forge(payload: object, header: object | None = None) -> str:
    """Build a token signed with the real secret (what a key-holder could mint)."""
    enc = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()  # noqa: E731
    signing_input = f"{enc(header or {'alg': 'HS256', 'typ': 'JWT'})}.{enc(payload)}"
    sig = hmac.new(settings.auth_secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


# ── 1. Demo credentials ─────────────────────────────────────────────────────


def test_demo_credentials_are_hidden_when_demo_mode_is_off(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    with TestClient(app) as raw:
        response = raw.get("/api/auth/demo")
    assert response.status_code == 404
    assert settings.demo_coordinator_password not in response.text


def test_demo_credentials_still_served_in_demo_mode():
    with TestClient(app) as raw:
        body = raw.get("/api/auth/demo").json()
    assert body["coordinator"]["password"] == settings.demo_coordinator_password


def test_default_password_demo_accounts_not_created_outside_demo_mode(monkeypatch):
    db = mongomock.MongoClient().db
    monkeypatch.setattr(settings, "demo_mode", False)
    assert auth.ensure_demo_user(db) is None
    assert auth.ensure_clinician_user(db) is None
    assert db.users.count_documents({}) == 0
    # A deliberately chosen password is fine.
    monkeypatch.setattr(settings, "demo_coordinator_password", "a-real-password-from-a-vault")
    assert auth.ensure_demo_user(db)["id"] == "user_coordinator"


# ── 3a. Secret defaults ─────────────────────────────────────────────────────


def test_default_auth_secret_is_replaced_outside_development():
    s = Settings(environment="production", auth_secret=DEFAULT_AUTH_SECRET, demo_mode=False,
                 demo_coordinator_password="x" * 20, demo_clinician_password="y" * 20)
    warnings = harden(s)
    assert s.auth_secret != DEFAULT_AUTH_SECRET and len(s.auth_secret) >= 32
    assert any("AUTH_SECRET" in w for w in warnings)


def test_development_keeps_its_secret_and_production_flags_demo_mode():
    dev = Settings(environment="development", auth_secret=DEFAULT_AUTH_SECRET)
    assert harden(dev) == [] and dev.auth_secret == DEFAULT_AUTH_SECRET
    prod = Settings(environment="production", auth_secret="z" * 48, demo_mode=True)
    assert any("DEMO_MODE is on" in w for w in harden(prod))
    short = Settings(environment="staging", auth_secret="change-me", demo_mode=True)
    assert any("shorter than 32" in w for w in harden(short))


# ── 3b. Tokens ──────────────────────────────────────────────────────────────


def test_token_roundtrip_and_expiry():
    token = auth.create_token(sub="user_coordinator", email="e", name="n", role="coordinator")
    assert auth.decode_token(token)["sub"] == "user_coordinator"
    expired = auth.create_token(sub="user_coordinator", email="e", name="n", role="coordinator", ttl_minutes=0)
    with pytest.raises(ValueError, match="expired"):
        auth.decode_token(expired)


@pytest.mark.parametrize(
    "payload, header",
    [
        ({"sub": "user_coordinator"}, None),  # no exp: would never expire
        ({"sub": "user_coordinator", "exp": "never"}, None),  # was a 500 (int("never"))
        ({"sub": "user_coordinator", "exp": {"$gt": 0}}, None),  # was a 500 (TypeError)
        ({"sub": "user_coordinator", "exp": True}, None),
        ([1, 2, 3], None),  # non-object payload: was AttributeError → 500
        ({"exp": time.time() + 60}, None),  # no subject
        ({"sub": "user_coordinator", "exp": time.time() + 60, "iat": time.time() + 3600}, None),
        ({"sub": "user_coordinator", "exp": time.time() + 60}, {"alg": "none"}),
    ],
)
def test_malformed_signed_tokens_are_rejected_cleanly(payload, header):
    with pytest.raises(ValueError):
        auth.decode_token(_forge(payload, header))


@pytest.mark.parametrize("token", ["", "a.b", "a.b.c", "x" * 10 + ".." + "y", "a.b.c.d", "!!.@@.##"])
def test_garbage_tokens_are_401_not_500(token):
    with TestClient(app) as raw:
        assert raw.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


@pytest.mark.parametrize("token", ["é.é.é", None, 42])
def test_non_ascii_or_non_string_tokens_raise_value_error(token):
    with pytest.raises(ValueError):
        auth.decode_token(token)


def test_tampered_signature_is_rejected():
    token = auth.create_token(sub="user_clinician", email="e", name="n", role="clinician")
    head, body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["role"] = "coordinator"
    forged_body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(ValueError, match="signature"):
        auth.decode_token(f"{head}.{forged_body}.{sig}")


def test_role_comes_from_the_database_not_the_token(client):
    # A valid token whose role claim says "coordinator" for the clinician account
    # must not unlock coordinator-only endpoints.
    token = auth.create_token(sub="user_clinician", email="e", name="n", role="coordinator")
    response = client.post("/api/admin/sweep", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_absurd_iteration_counts_are_refused():
    salt = "00" * 16
    assert not auth.verify_password("x", f"pbkdf2_sha256$999999999${salt}${'00' * 32}")
    assert not auth.verify_password("x", "pbkdf2_sha256$notanumber$zz$zz")


# ── 3c. Login ───────────────────────────────────────────────────────────────


def test_unknown_email_costs_the_same_pbkdf2_work(monkeypatch):
    calls = []
    real = auth.verify_password
    monkeypatch.setattr(auth, "verify_password", lambda p, h: calls.append(h) or real(p, h))
    assert auth.authenticate(mongomock.MongoClient().db, "nobody@example.test", "pw") is None
    assert calls == [auth._DUMMY_HASH]


def test_login_normalises_email(client):
    response = client.post(
        "/api/auth/login",
        json={"email": f"  {settings.demo_coordinator_email.upper()}  ", "password": settings.demo_coordinator_password},
    )
    assert response.status_code == 200


def test_repeated_failures_are_throttled_then_reset_by_success(client):
    email = settings.demo_coordinator_email
    for _ in range(auth.login_throttle.max_failures):
        assert client.post("/api/auth/login", json={"email": email, "password": "wrong"}).status_code == 401
    blocked = client.post("/api/auth/login", json={"email": email, "password": settings.demo_coordinator_password})
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    # Another account from the same client is unaffected.
    other = client.post(
        "/api/auth/login",
        json={"email": settings.demo_clinician_email, "password": settings.demo_clinician_password},
    )
    assert other.status_code == 200
    auth.login_throttle.reset()
    assert client.post("/api/auth/login", json={"email": email, "password": settings.demo_coordinator_password}).status_code == 200


def test_oversized_credentials_are_rejected_before_hashing(client):
    response = client.post("/api/auth/login", json={"email": "a@b.c", "password": "x" * 100_000})
    assert response.status_code == 422


# ── Voice endpoints ─────────────────────────────────────────────────────────


def test_call_sync_requires_sign_in_and_a_sane_id(client, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    with TestClient(app) as raw:
        assert raw.post("/api/voice/calls/conv_1/sync").status_code == 401
    assert client.post("/api/voice/calls/conv_1/sync").status_code == 200
    assert client.post("/api/voice/calls/conv%3Fx%3D1/sync").status_code == 422


def test_voice_api_fails_closed_outside_development(client, monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "continuum_voice_secret", None)
    response = client.post("/api/voice/identify", json={"given_name": "Michael", "family_name": "Chen"})
    assert response.status_code == 503
    assert "Michael" not in response.text


def test_post_call_requires_signature_when_webhook_secret_is_set(client, monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_webhook_secret", "whsec_test")
    unsigned = client.post("/api/voice/post-call", json={"type": "post_call_transcription", "data": {}})
    assert unsigned.status_code == 401
    raw = json.dumps({"type": "ping"}).encode()
    ts = str(int(time.time()))
    sig = hmac.new(b"whsec_test", f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    signed = client.post(
        "/api/voice/post-call", content=raw,
        headers={"content-type": "application/json", "elevenlabs-signature": f"t={ts},v1={sig}"},
    )
    assert signed.status_code == 200


# ── 2. Scheduler: preferred channel ─────────────────────────────────────────


@pytest.fixture()
def telephony(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    monkeypatch.setattr(settings, "elevenlabs_agent_id", "a")
    monkeypatch.setattr(settings, "elevenlabs_phone_number_id", "p")
    dialled: list[dict] = []

    class Ok:
        status_code = 200

        def json(self):
            return {"success": True, "conversation_id": f"conv_{len(dialled)}"}

    def fake_post(url, json, headers, timeout):
        dialled.append(json)
        return Ok()

    monkeypatch.setattr(scheduler.httpx, "post", fake_post)
    return dialled


@pytest.mark.parametrize("channel", ["Text", "Email", "Portal", "Caregiver"])
def test_non_voice_channel_is_never_dialled(client, db, telephony, channel):
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142", "preferences.channel": channel}})
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_at(15))

    assert telephony == [], "patient asked not to be phoned"
    stored = db.checkins.find_one({"id": chk["id"]})
    assert stored["status"] == "awaiting_outreach" and stored["channel"] == channel
    tasks = [a for a in repo.list_approvals(db, MICHAEL, status="pending") if a.get("source") == "scheduler"]
    task = next(t for t in tasks if t["title"] == f"Contact Michael Chen by {channel} for a scheduled check-in")
    # Through _queue_task, so the ledger records the hand-off.
    action = db.actions.find_one({"key": f"task:{task['id']}"})
    assert action["kind"] == "coordinator_task" and action["status"] == ledger.SENT
    assert not db.actions.find_one({"kind": "outbound_call", "patient_id": MICHAEL})
    # Pending outreach counts as "in flight": no duplicate check-in gets scheduled.
    assert scheduler._has_pending_checkin(db, MICHAEL, chk.get("obligation_id")) or chk.get("obligation_id") is None


def test_voice_channel_still_dials(client, db, telephony):
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142", "preferences.channel": "Voice call"}})
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_at(15))
    assert len(telephony) == 1
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "dispatched"


def test_missing_channel_defaults_to_voice(client, db, telephony):
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142"}, "$unset": {"preferences.channel": ""}})
    repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_at(15))
    assert len(telephony) == 1


# ── Scheduler: calling hours ────────────────────────────────────────────────


def test_no_automated_calls_at_night(client, db, telephony):
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142", "preferences.channel": "Voice call"}})
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_at(2))  # 2am local
    assert telephony == []
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "scheduled"
    run_sweep(today=date.today(), now=_at(10))
    assert any(c["conversation_initiation_client_data"]["dynamic_variables"]["checkin_id"] == chk["id"] for c in telephony)
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "dispatched"


def test_calling_window_logic(monkeypatch):
    monkeypatch.setattr(settings, "call_window_start_hour", 9)
    monkeypatch.setattr(settings, "call_window_end_hour", 20)
    assert scheduler.within_calling_hours(_at(9)) and not scheduler.within_calling_hours(_at(20))
    monkeypatch.setattr(settings, "call_window_start_hour", 22)
    monkeypatch.setattr(settings, "call_window_end_hour", 2)
    assert scheduler.within_calling_hours(_at(23)) and not scheduler.within_calling_hours(_at(12))
    monkeypatch.setattr(settings, "call_timezone", "Not/AZone")
    scheduler.within_calling_hours(datetime.now(timezone.utc))  # falls back to UTC, no crash


# ── Scheduler: concurrency and uncertain outcomes ───────────────────────────


def test_a_checkin_claimed_by_another_sweep_is_left_alone(client, db, telephony):
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142", "preferences.channel": "Voice call"}})
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    now = _at(15)
    db.checkins.update_one({"id": chk["id"]}, {"$set": {"status": "dispatching", "claimed_at": now.isoformat()}})
    run_sweep(today=date.today(), now=now)
    assert telephony == [] and db.checkins.find_one({"id": chk["id"]})["status"] == "dispatching"


def test_claim_is_atomic(client, db):
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    snapshot = db.checkins.find_one({"id": chk["id"]}, {"_id": 0})
    now = _at(15)
    assert scheduler._claim(db, snapshot, now) is True
    assert scheduler._claim(db, snapshot, now) is False  # second sweep with the same stale read


def test_stale_claim_after_a_crash_never_redials(client, db, telephony):
    """A sweep died after recording intent: the next one takes over but won't dial."""
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142", "preferences.channel": "Voice call"}})
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    now = _at(15)
    old = (now - timedelta(minutes=30)).isoformat()
    db.checkins.update_one({"id": chk["id"]}, {"$set": {"status": "dispatching", "claimed_at": old}})
    ledger.intend(db, kind="outbound_call", key=f"checkin:{chk['id']}:call", patient_id=MICHAEL,
                  summary="s", proof="p", actor="t")
    run_sweep(today=date.today(), now=now)
    assert telephony == []
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "awaiting_manual_call"
    assert db.actions.find_one({"key": f"checkin:{chk['id']}:call"})["status"] == ledger.UNKNOWN


@pytest.mark.parametrize(
    "outcome, ledger_status",
    [
        (httpx.ReadTimeout("slow"), ledger.UNKNOWN),
        (503, ledger.UNKNOWN),
        (400, ledger.FAILED),
        (httpx.ConnectError("refused"), ledger.FAILED),
    ],
)
def test_ambiguous_call_outcomes_are_unknown_not_failed(client, db, monkeypatch, outcome, ledger_status):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "k")
    monkeypatch.setattr(settings, "elevenlabs_agent_id", "a")
    monkeypatch.setattr(settings, "elevenlabs_phone_number_id", "p")
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142", "preferences.channel": "Voice call"}})

    class Resp:
        status_code = outcome if isinstance(outcome, int) else 200

        def json(self):
            return {}

    def fake_post(*a, **k):
        if isinstance(outcome, Exception):
            raise outcome
        return Resp()

    monkeypatch.setattr(scheduler.httpx, "post", fake_post)
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_at(15))
    assert db.actions.find_one({"key": f"checkin:{chk['id']}:call"})["status"] == ledger_status
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "awaiting_manual_call"


def test_checkin_for_missing_patient_is_flagged(client, db):
    chk = repo.schedule_checkin(db, {"patient_id": "pat_ghost", "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_at(15))
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "needs_review"


def test_missing_consent_flag_means_no_automated_call(client, db, telephony):
    db.patients.update_one(
        {"id": MICHAEL},
        {"$set": {"phone": "+1 416 555 0142", "preferences.channel": "Voice call"},
         "$unset": {"preferences.consent_to_automated_calls": ""}},
    )
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_at(15))
    assert telephony == []
    assert db.checkins.find_one({"id": chk["id"]})["status"] == "skipped_no_consent"
    callback = client.post("/api/voice/checkin", json={"patient_id": MICHAEL, "when": "today", "reason": "x"}).json()
    assert callback["ok"] is False


def test_scoped_tokens_are_not_sessions(client):
    ticket = auth.create_token(sub="user_coordinator", email="e", name="n", role="coordinator",
                               ttl_minutes=1, scope="stream")
    assert auth.decode_token(ticket)["scope"] == "stream"
    with TestClient(app) as raw:
        assert raw.get("/api/auth/me", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401


# ── Phone numbers and held check-ins ────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("+1 416 555 0142", "+14165550142"),
        ("416 555 0142", "+14165550142"),  # was "+4165550142": a wrong number
        ("(416) 555-0142", "+14165550142"),
        ("1-416-555-0142", "+14165550142"),
        ("+44 20 7946 0958", "+442079460958"),
        ("0044 20 7946 0958", "+442079460958"),
        ("+1 416 555 0142 (synthetic)", None),
        ("555 0142", None),  # too short to be a full number
        ("0416 555 0142", None),  # ambiguous trunk prefix
        ("+0 123 456 789", None),
        ("+1 234 567 890 123 456 7", None),  # > 15 digits
        ("", None),
        (None, None),
    ],
)
def test_phone_numbers_are_normalised_or_refused(raw, expected):
    assert scheduler._dialable(raw) == expected


def test_national_numbers_need_a_plus_outside_north_america(monkeypatch):
    monkeypatch.setattr(settings, "default_country_code", "44")
    assert scheduler._dialable("020 7946 0958") is None
    assert scheduler._dialable("+44 20 7946 0958") == "+442079460958"


def test_number_saved_without_country_code_dials_correctly(client, db, telephony):
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "416 555 0142", "preferences.channel": "Voice call"}})
    repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    run_sweep(today=date.today(), now=_at(15))
    assert telephony and telephony[0]["to_number"] == "+14165550142"


def test_held_checkin_is_explained_in_the_sweep_and_on_the_checkin(client, db, telephony):
    db.patients.update_one({"id": MICHAEL}, {"$set": {"phone": "+1 416 555 0142", "preferences.channel": "Voice call"}})
    chk = repo.schedule_checkin(db, {"patient_id": MICHAEL, "scheduled_for": date.today().isoformat(), "reason": "x"})
    result = run_sweep(today=date.today(), now=_at(22))

    held = [a for a in result["actions"] if a["type"] == "checkin_held" and a["checkin_id"] == chk["id"]]
    assert held and held[0]["reason"] == "outside_calling_hours"
    assert held[0]["calls_after"] == _at(9, date.today() + timedelta(days=1)).isoformat()
    assert result["calling_hours"]["open_now"] is False
    stored = db.checkins.find_one({"id": chk["id"]})
    assert stored["status"] == "scheduled" and "Waiting for calling hours" in stored["dispatch_detail"]
    # Holds are not counted as actions taken.
    assert db.scheduler_runs.find_one({"held": {"$gte": 1}})  # ran_at ties at 1 s resolution

    run_sweep(today=date.today() + timedelta(days=1), now=_at(9, date.today() + timedelta(days=1)))
    stored = db.checkins.find_one({"id": chk["id"]})
    assert stored["status"] == "dispatched" and "held_until" not in stored


def test_next_window_before_opening_is_same_day():
    assert scheduler.next_calling_window(_at(6)) == _at(9)
    assert scheduler.next_calling_window(_at(12)) == _at(12)
