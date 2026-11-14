"""Minimal FHIR R4 REST client.

Auth modes:

* ``none``         open servers (local HAPI, Oracle Health open sandbox)
* ``bearer``       a token you already have (manual testing)
* ``backend_jwt``  SMART Backend Services (Epic "Backend Systems" apps): a
                   signed RS384 client assertion is exchanged for an access
                   token with the client_credentials grant.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger("continuum.ingest.fhir")

FHIR_JSON = "application/fhir+json"
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


class FHIRError(RuntimeError):
    pass


class FHIRClient:
    def __init__(
        self,
        base_url: str,
        *,
        auth_mode: str = "none",
        bearer_token: str | None = None,
        client_id: str | None = None,
        token_url: str | None = None,
        private_key: str | None = None,
        key_id: str | None = None,
        scopes: str = "system/*.read",
        transport: httpx.BaseTransport | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth_mode = auth_mode
        self.bearer_token = bearer_token
        self.client_id = client_id
        self.token_url = token_url
        self.private_key = private_key
        self.key_id = key_id
        self.scopes = scopes
        self._token: str | None = None
        self._token_expiry = 0.0
        # Searches run concurrently (pull, poller); one token request at a time.
        self._token_lock = threading.Lock()
        self._http = httpx.Client(timeout=timeout, transport=transport)

    # ── auth ────────────────────────────────────────────────────────────

    def client_assertion(self, now: float | None = None) -> str:
        """Signed JWT proving our identity to the token endpoint."""
        import jwt  # pyjwt

        if not (self.client_id and self.token_url and self.private_key):
            raise FHIRError("backend_jwt auth needs client_id, token_url and a private key")
        now = now or time.time()
        claims = {
            "iss": self.client_id,
            "sub": self.client_id,
            "aud": self.token_url,
            "jti": uuid.uuid4().hex,
            "iat": int(now),
            "nbf": int(now),
            "exp": int(now) + 300,  # Epic rejects assertions valid for more than 5 minutes
        }
        headers = {"typ": "JWT"}
        if self.key_id:
            headers["kid"] = self.key_id
        return jwt.encode(claims, self.private_key, algorithm="RS384", headers=headers)

    def _access_token(self) -> str | None:
        if self.auth_mode == "bearer":
            return self.bearer_token
        if self.auth_mode != "backend_jwt":
            return None
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        with self._token_lock:
            if self._token and time.time() < self._token_expiry - 30:
                return self._token
            return self._fetch_token()

    def _fetch_token(self) -> str:
        response = self._http.post(
            self.token_url or "",
            data={
                "grant_type": "client_credentials",
                "client_assertion_type": ASSERTION_TYPE,
                "client_assertion": self.client_assertion(),
                "scope": self.scopes,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            raise FHIRError(f"Token request failed ({response.status_code}): {response.text[:300]}")
        body = response.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + float(body.get("expires_in", 300))
        return self._token

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": FHIR_JSON}
        token = self._access_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ── REST ────────────────────────────────────────────────────────────

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> dict | None:
        try:
            response = self._http.get(self._url(path_or_url), params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise FHIRError(f"FHIR request failed: {exc}") from exc
        if response.status_code in (404, 410):
            return None
        if response.status_code >= 400:
            raise FHIRError(f"FHIR {response.status_code} for {path_or_url}: {response.text[:300]}")
        return response.json()

    def read(self, resource_type: str, resource_id: str) -> dict | None:
        return self.get(f"{resource_type}/{resource_id}")

    def search(self, resource_type: str, params: dict[str, Any] | None = None, max_pages: int = 5) -> list[dict]:
        out: list[dict] = []
        bundle = self.get(resource_type, params=params)
        pages = 0
        while bundle and pages < max_pages:
            for entry in bundle.get("entry") or []:
                resource = entry.get("resource") or {}
                mode = (entry.get("search") or {}).get("mode", "match")
                if resource.get("resourceType") == resource_type and mode == "match":
                    out.append(resource)
            pages += 1
            next_link = next((l.get("url") for l in bundle.get("link") or [] if l.get("relation") == "next"), None)
            bundle = self.get(next_link) if next_link else None
        return out

    def close(self) -> None:
        self._http.close()


def ehr_configured() -> bool:
    return bool(settings.ehr_fhir_base_url)


def client_from_settings() -> FHIRClient | None:
    if not settings.ehr_fhir_base_url:
        return None
    private_key = None
    if settings.ehr_private_key_path:
        with open(settings.ehr_private_key_path, encoding="utf-8") as fh:
            private_key = fh.read()
    return FHIRClient(
        settings.ehr_fhir_base_url,
        auth_mode=settings.ehr_auth_mode,
        bearer_token=settings.ehr_bearer_token,
        client_id=settings.ehr_client_id,
        token_url=settings.ehr_token_url,
        private_key=private_key,
        key_id=settings.ehr_key_id,
        scopes=settings.ehr_scopes,
    )
