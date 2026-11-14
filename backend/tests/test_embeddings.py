"""Embedding backend selection: remote when it works, local when it doesn't."""

from __future__ import annotations

from app.agent import llm
from app.config import settings


def _reset():
    llm.get_embeddings.cache_clear()
    llm._embed_failures = 0


class _Broken:
    dimensions = 1536

    def embed_query(self, text):
        raise RuntimeError("Error code: 404")


class _Local:
    model_name = "fake-local"
    dimensions = 4

    def __init__(self, *_):
        pass

    def embed_query(self, text):
        return [0.1, 0.2, 0.3, 0.4]

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


def test_auto_falls_back_to_local_when_endpoint_has_no_embeddings(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "auto")
    monkeypatch.setattr(llm, "_remote_embeddings", lambda: _Broken())
    monkeypatch.setattr(llm, "_local_embeddings", lambda: _Local())
    _reset()
    try:
        assert isinstance(llm.get_embeddings(), _Local)
        assert llm.embedding_dimensions() == 4
        assert llm.embed_text("hello") == [0.1, 0.2, 0.3, 0.4]
    finally:
        _reset()


def test_repeated_failures_stop_calling(monkeypatch):
    calls = {"n": 0}

    class _Flaky(_Local):
        def embed_query(self, text):
            calls["n"] += 1
            raise RuntimeError("down")

    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(llm, "_local_embeddings", lambda: _Flaky())
    _reset()
    try:
        for _ in range(10):
            assert llm.embed_text("x") is None
        assert calls["n"] == 3
    finally:
        _reset()


def test_none_disables_embeddings(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "none")
    _reset()
    try:
        assert llm.get_embeddings() is None and llm.embed_text("x") is None
    finally:
        _reset()


def test_vector_index_definition_is_vector_search_with_patient_filter():
    from app.db import _vector_index_definition

    fields = _vector_index_definition(384)["fields"]
    assert {"type": "vector", "path": "embedding", "numDimensions": 384, "similarity": "cosine"} in fields
    assert {"type": "filter", "path": "patient_id"} in fields
