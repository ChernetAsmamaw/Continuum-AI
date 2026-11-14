"""Auth regression tests: login, token guard, and the /me endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_is_public():
    with TestClient(app) as raw:
        assert raw.get("/api/health").status_code == 200


def test_data_endpoint_requires_auth():
    with TestClient(app) as raw:
        assert raw.get("/api/dashboard").status_code == 401
        assert raw.get("/api/patients/pat_michael/journey").status_code == 401


def test_demo_credential_is_exposed():
    with TestClient(app) as raw:
        body = raw.get("/api/auth/demo").json()
        for role, cred in body.items():
            assert cred["email"] and cred["password"] and cred["name"]
            assert cred["role"] == role
        assert set(body) == {"coordinator", "clinician"}


def test_login_wrong_password_is_rejected():
    with TestClient(app) as raw:
        cred = raw.get("/api/auth/demo").json()["coordinator"]
        response = raw.post("/api/auth/login", json={"email": cred["email"], "password": "wrong"})
        assert response.status_code == 401


def test_login_and_me_roundtrip():
    with TestClient(app) as raw:
        cred = raw.get("/api/auth/demo").json()["coordinator"]
        login = raw.post(
            "/api/auth/login", json={"email": cred["email"], "password": cred["password"]}
        )
        assert login.status_code == 200
        payload = login.json()
        assert payload["token"]
        assert payload["user"]["email"] == cred["email"]

        headers = {"Authorization": f"Bearer {payload['token']}"}
        me = raw.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["user"]["name"] == cred["name"]

        dashboard = raw.get("/api/dashboard", headers=headers)
        assert dashboard.status_code == 200


def test_invalid_token_is_rejected():
    with TestClient(app) as raw:
        response = raw.get("/api/dashboard", headers={"Authorization": "Bearer not-a-token"})
        assert response.status_code == 401
