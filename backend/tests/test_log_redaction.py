import logging

from app.main import RedactQuerySecrets, redact_secrets


def test_access_log_redacts_stream_token(caplog):
    access = logging.getLogger("uvicorn.access")
    assert any(isinstance(f, RedactQuerySecrets) for f in access.filters)
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        access.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET",
                    "/api/stream?token=eyJhbGciOi.payload.sig&x=1", "1.1", 200)
    line = caplog.records[-1].getMessage()
    assert "eyJhbGciOi" not in line
    assert "/api/stream?token=[REDACTED]&x=1" in line


def test_redaction_leaves_other_query_values():
    assert redact_secrets("/api/patients?status=open") == "/api/patients?status=open"
    assert redact_secrets("/x?a=1&access_token=abc") == "/x?a=1&access_token=[REDACTED]"


def test_stream_still_rejects_bad_tokens(client):
    assert client.get("/api/stream", params={"token": "nope"}).status_code == 401
