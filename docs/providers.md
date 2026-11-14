# Model providers

Continuum is provider-agnostic. One setting picks the chat model, one picks
embeddings; LangChain's generic factories (`init_chat_model`,
`init_embeddings`) do the rest. With no key, every agent step falls back to
deterministic rules, so the app always runs.

| Provider | `.env` | Default model |
| --- | --- | --- |
| Gemini (key from [Google AI Studio](https://aistudio.google.com/apikey), nothing else needed) | `LLM_PROVIDER=gemini`, `LLM_API_KEY=<key>` | `gemini-3.5-flash-lite` |
| OpenAI / OpenAI-compatible | `LLM_PROVIDER=openai`, `LLM_API_KEY`, optional `LLM_BASE_URL` | `gpt-4.1-mini` |
| Anthropic | `LLM_PROVIDER=anthropic`, `LLM_API_KEY` | `claude-sonnet-5-5` |
| Others (Ollama, Groq, Mistral, Bedrock) | `LLM_PROVIDER=<name>` + that provider's package and key | see `agent/llm.py` |

Set `LLM_MODEL` to override the default (e.g. `gemini-2.5-pro`).

**Embeddings:** `EMBEDDING_PROVIDER=auto` follows the chat provider (Gemini
chat → `gemini-embedding-001`, OpenAI →
`text-embedding-3-small`), then falls back to the built-in local model
(`BAAI/bge-small-en-v1.5`). You can also set `voyageai`, `cohere`, `local` or
`none`. Dimensions are measured, and the Atlas Vector Search index is rebuilt
automatically if they change.

**LangSmith:** set `LANGSMITH_API_KEY` (and optionally `LANGSMITH_PROJECT`);
tracing turns on automatically. Set `LANGSMITH_TRACING=false` to opt out.

**Check everything:** `cd backend && .venv/bin/python -m app.keys_check`
(`--offline` checks configuration only). It reports configured / working /
failing for the LLM, embeddings, LangSmith, ElevenLabs, MongoDB and the EHR,
and what each one unlocks.
