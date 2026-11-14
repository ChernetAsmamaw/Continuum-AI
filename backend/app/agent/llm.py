"""LangChain chat-model and embedding factories with graceful fallbacks."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from functools import lru_cache

from ..config import settings

log = logging.getLogger("continuum.llm")


# ── Providers ──────────────────────────────────────────────────────────────
#
# One setting picks the provider; LangChain's generic factories do the rest, so
# switching OpenAI → Gemini → Claude → a local Ollama is a .env
# change. Every agent step has a deterministic fallback when no model is set.

PROVIDER_ALIASES = {
    "gemini": "google_genai", "google": "google_genai", "google_ai": "google_genai",
    "claude": "anthropic", "openai_compatible": "openai", "azure": "azure_openai",
    "voyage": "voyageai",
}
DEFAULT_CHAT_MODELS = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-5-5",
    "google_genai": "gemini-3.5-flash-lite",
    "ollama": "llama3.1",
    "groq": "llama-3.3-70b-versatile",
    "mistralai": "mistral-large-latest",
}
DEFAULT_EMBEDDING_MODELS = {
    "openai": "text-embedding-3-small",
    "google_genai": "models/gemini-embedding-001",
    "voyageai": "voyage-3.5",
    "cohere": "embed-english-v3.0",
    "ollama": "nomic-embed-text",
}
# Env vars each provider reads on its own (checked so "configured?" is honest).
PROVIDER_KEY_ENVS = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "google_genai": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "mistralai": ["MISTRAL_API_KEY"],
    "voyageai": ["VOYAGE_API_KEY"],
    "cohere": ["COHERE_API_KEY"],
}


def normalize_provider(name: str | None) -> str:
    key = (name or "").strip().lower().replace("-", "_")
    return PROVIDER_ALIASES.get(key, key)


def chat_provider() -> str:
    return normalize_provider(settings.llm_provider) or "openai"


def chat_model_name() -> str:
    return settings.llm_model or DEFAULT_CHAT_MODELS.get(chat_provider(), "")


def _env_key(provider: str) -> str | None:
    import os

    for name in PROVIDER_KEY_ENVS.get(provider, []):
        if os.getenv(name):
            return os.getenv(name)
    return None


def provider_credentials(provider: str, explicit_key: str | None = None) -> tuple[bool, str]:
    """(configured?, how) without making a network call."""
    if provider == "ollama":
        return (True, f"Ollama at {settings.llm_base_url or 'http://localhost:11434'}")
    if provider == "openai" and settings.llm_base_url and not (explicit_key or _env_key("openai")):
        return (True, f"OpenAI-compatible server at {settings.llm_base_url} (no key)")
    key = explicit_key or _env_key(provider)
    return (bool(key), "API key set" if key else f"set LLM_API_KEY or {' / '.join(PROVIDER_KEY_ENVS.get(provider, ['the provider key']))}")


def chat_configured() -> bool:
    return provider_credentials(chat_provider(), settings.llm_api_key)[0]


def _provider_kwargs(provider: str, key: str | None, base_url: str | None) -> dict:
    kwargs: dict = {}
    if key:
        kwargs["google_api_key" if provider == "google_genai" else "api_key"] = key
    if base_url and provider in {"openai", "ollama", "anthropic"}:
        kwargs["base_url"] = base_url
    return kwargs


@lru_cache
def get_chat_model():
    """Return a LangChain chat model for the configured provider, or None."""
    provider = chat_provider()
    if not chat_configured():
        return None
    try:
        from langchain.chat_models import init_chat_model

        kwargs = {"temperature": 0, "max_tokens": 2000, **_provider_kwargs(provider, settings.llm_api_key, settings.llm_base_url)}
        if provider == "openai" and settings.llm_disable_thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        model = init_chat_model(chat_model_name(), model_provider=provider, **kwargs)
        log.info("Chat model: %s:%s", provider, chat_model_name())
        return model
    except Exception as exc:  # pragma: no cover - missing optional provider package / bad config
        log.warning("Could not initialise chat model %s:%s: %s", provider, chat_model_name(), exc)
        return None


class LocalEmbeddings:
    """LangChain-compatible embeddings from a local fastembed model (CPU, no key)."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self.model_name = model_name
        self._model = TextEmbedding(model_name)
        self.dimensions = len(next(iter(self._model.embed(["dimension probe"]))))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed([t[:8000] for t in texts])]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text[:8000]))).tolist()

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """``embed_query`` for many texts in one model call (same vectors)."""
        return [v.tolist() for v in self._model.query_embed([t[:8000] for t in texts])]


def embedding_provider_name() -> str:
    """The remote embedding provider ``auto`` would try (follows the chat provider)."""
    configured = normalize_provider(settings.embedding_provider)
    if configured not in {"", "auto"}:
        return configured
    chat = chat_provider()
    return chat if chat in DEFAULT_EMBEDDING_MODELS else "openai"


def _remote_embeddings(provider: str):
    """Any LangChain embeddings provider; dimensions are measured, not assumed."""
    key = settings.embedding_api_key or (settings.llm_api_key if provider == chat_provider() else None)
    if not provider_credentials(provider, key)[0]:
        return None
    configured_model = settings.embedding_model
    if provider != "openai" and configured_model == "text-embedding-3-small":
        configured_model = ""  # the OpenAI default doesn't apply to other providers
    model_name = configured_model or DEFAULT_EMBEDDING_MODELS.get(provider, "")
    base_url = settings.embedding_base_url or (settings.llm_base_url if provider == chat_provider() else None)
    if provider == "voyageai":
        from langchain_voyageai import VoyageAIEmbeddings

        model = VoyageAIEmbeddings(model=model_name, **({"voyage_api_key": key} if key else {}))
    elif provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        kwargs = {"model": model_name, "dimensions": settings.embedding_dimensions}
        if key or _env_key("openai"):
            kwargs["api_key"] = key or _env_key("openai")
        if base_url:
            kwargs["base_url"] = base_url
        model = OpenAIEmbeddings(**kwargs)
    else:
        from langchain.embeddings import init_embeddings

        model = init_embeddings(model_name, provider=provider, **_provider_kwargs(provider, key, base_url))
    model.dimensions = len(model.embed_query("dimension probe"))  # type: ignore[attr-defined]
    model.provider_label = f"{provider}:{model_name}"  # type: ignore[attr-defined]
    return model


def _local_embeddings():
    try:
        model = LocalEmbeddings(settings.local_embedding_model)
        log.info("Using local embeddings %s (%d dims).", model.model_name, model.dimensions)
        return model
    except Exception as exc:  # pragma: no cover - optional dependency
        log.warning("Local embeddings unavailable: %s", exc)
        return None


_embeddings_lock = threading.Lock()


_UNSET = object()
_embeddings = _UNSET


def get_embeddings():
    """Pick an embedding backend once per process (thread-safe)."""
    global _embeddings
    if _embeddings is _UNSET:
        with _embeddings_lock:
            if _embeddings is _UNSET:
                _embeddings = _pick_embeddings()
    return _embeddings


def _clear_embeddings() -> None:
    global _embeddings
    _embeddings = _UNSET
    _vector_cache.clear()


get_embeddings.cache_clear = _clear_embeddings  # type: ignore[attr-defined]


def _pick_embeddings():
    """Choose the embedding backend.

    ``auto`` tries the remote provider that matches the chat provider (Gemini
    chat → Gemini embeddings, …) once, then falls back to the built-in local
    model, so a misconfigured key never breaks memory writes.
    """
    provider = normalize_provider(settings.embedding_provider) or "auto"
    if provider == "none":
        return None
    if provider == "local":
        return _local_embeddings()
    target = embedding_provider_name()
    try:
        remote = _remote_embeddings(target)
        if remote is not None:
            log.info("Using remote embeddings %s (%d dims).", remote.provider_label, remote.dimensions)
            return remote
        if provider != "auto":
            log.warning("Embedding provider %s has no credentials; falling back to the local model.", target)
    except Exception as exc:
        log.info("Remote embeddings %s unavailable (%s); falling back to the local model.", target, str(exc)[:160])
    return _local_embeddings()


def embedding_label() -> str:
    model = get_embeddings()
    if model is None:
        return "none (lexical recall)"
    if isinstance(model, LocalEmbeddings):
        return f"local:{model.model_name}"
    return getattr(model, "provider_label", type(model).__name__)


def embedding_dimensions() -> int | None:
    model = get_embeddings()
    return getattr(model, "dimensions", None) if model is not None else None


def llm_available() -> bool:
    return get_chat_model() is not None


_embed_failures = 0

# Embeddings are a pure function of (model, text); the same text is often
# embedded more than once per request (e.g. the patient summary memory is
# refreshed at the start and end of an agent run). Small LRU, per process,
# cleared whenever the model is re-picked.
_VECTOR_CACHE_SIZE = 1024
_vector_cache: "OrderedDict[str, list[float]]" = OrderedDict()
_vector_cache_lock = threading.Lock()


def _cached(key: str) -> list[float] | None:
    with _vector_cache_lock:
        vector = _vector_cache.get(key)
        if vector is not None:
            _vector_cache.move_to_end(key)
            return list(vector)
    return None


def _remember(key: str, vector: list[float]) -> None:
    with _vector_cache_lock:
        _vector_cache[key] = list(vector)
        _vector_cache.move_to_end(key)
        while len(_vector_cache) > _VECTOR_CACHE_SIZE:
            _vector_cache.popitem(last=False)


def _embed_many(model, texts: list[str]) -> list[list[float]]:
    if hasattr(model, "embed_queries"):
        return model.embed_queries(texts)
    try:
        from langchain_openai import OpenAIEmbeddings

        if isinstance(model, OpenAIEmbeddings):
            # OpenAIEmbeddings.embed_query is embed_documents([text])[0].
            return model.embed_documents(texts)
    except Exception:  # pragma: no cover - optional dependency
        pass
    return [model.embed_query(t) for t in texts]


def _note_failure(exc: Exception) -> None:
    global _embed_failures
    _embed_failures += 1
    if _embed_failures == 1 or _embed_failures == 3:
        log.warning("Embedding failed (%s)%s", exc, "; disabling embeddings for this process" if _embed_failures == 3 else "")


def embed_text(text: str) -> list[float] | None:
    """Embed one text; after repeated failures, stop trying (lexical recall still works)."""
    global _embed_failures
    model = get_embeddings()
    if model is None or _embed_failures >= 3:
        return None
    key = text[:8000]
    hit = _cached(key)
    if hit is not None:
        return hit
    try:
        vector = model.embed_query(key)
        _embed_failures = 0
        _remember(key, vector)
        return vector
    except Exception as exc:  # pragma: no cover - network dependent
        _note_failure(exc)
        return None


def embed_texts(texts: list[str]) -> list[list[float] | None]:
    """``embed_text`` for many texts with one model call for the uncached ones."""
    global _embed_failures
    model = get_embeddings()
    if model is None or _embed_failures >= 3 or not texts:
        return [None] * len(texts)
    keys = [t[:8000] for t in texts]
    out: list[list[float] | None] = [_cached(k) for k in keys]
    todo = list(dict.fromkeys(k for k, v in zip(keys, out) if v is None))
    if todo:
        try:
            vectors = dict(zip(todo, _embed_many(model, todo)))
            _embed_failures = 0
        except Exception as exc:  # pragma: no cover - network dependent
            _note_failure(exc)
            return out
        for key, vector in vectors.items():
            _remember(key, vector)
        out = [v if v is not None else list(vectors[k]) for k, v in zip(keys, out)]
    return out


def message_text(message) -> str:
    """Plain text of a chat reply.

    Newer models (Gemini 3.x, Anthropic) return ``content`` as a list of parts
    (``[{"type": "text", "text": ...}, ...]``) instead of a string; join the
    text parts so callers never show the raw list.
    """
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return "" if content is None else str(content)
