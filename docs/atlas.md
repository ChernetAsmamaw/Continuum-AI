# MongoDB Atlas setup

Continuum uses MongoDB Atlas for structured state, append-only audit records and
semantic recall. LangGraph checkpoints are stored in the same database through
`MongoDBSaver`.

If Atlas is not available, set `CONTINUUM_DB_MODE=memory` and the API uses an
in-process `mongomock` database. In that mode vector search is disabled and
memory recall uses lexical scoring, so the full demo still works.

## Quick switch (hackathon Atlas Sandbox)

1. In the sandbox organization: create a project and cluster, add a
   **database user** (Database Access) and your IP (Network Access; `0.0.0.0/0`
   is fine for the event).
2. Atlas → **Connect → Drivers → Python**: copy the `mongodb+srv://…` string.
3. In `.env`:
   ```bash
   MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
   MONGODB_DB=continuum
   CONTINUUM_DB_MODE=atlas
   EMBEDDING_PROVIDER=auto
   ```
   URL-encode special characters in the password (`@` → `%40`, `:` → `%3A`, `/` → `%2F`).
4. Run the setup and check:
   ```bash
   cd backend && .venv/bin/python -m app.atlas_setup
   ```
   It connects (and explains the usual failures in plain English), creates all
   indexes, the demo users and seed data, embeds memories, creates the
   **Vector Search** index and waits until it is queryable. It then runs a
   real patient-filtered `$vectorSearch` and confirms no other patient's
   memories come back. `--check` only connects and reports.
5. Restart the API. `/api/health` should show `"database": "connected"` and
   `"vector_search": true`.

To rehearse without Atlas, MongoDB's Atlas Local image supports Vector Search:
`docker run -d -p 27018:27017 -e MONGODB_INITDB_ROOT_USERNAME=continuum -e MONGODB_INITDB_ROOT_PASSWORD=localtest mongodb/mongodb-atlas-local`
and `python -m app.atlas_setup --uri "mongodb://continuum:localtest@localhost:27018/?directConnection=true"`.

## 1. Connection

```bash
MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB=continuum
CONTINUUM_DB_MODE=atlas
```

Create an Atlas database user with read/write access to the `continuum`
database. The app creates its normal indexes on startup (`bootstrap()` in
`backend/app/db.py`).

## 2. Collections

| Collection | Purpose |
| --- | --- |
| `patients` | Patient profiles, contact details and consent preferences |
| `care_episodes` | Admission/discharge episodes |
| `events` | Append-only interaction timeline |
| `obligations` | Follow-up obligations and status history |
| `evidence` | Patient reports, clinic confirmations, overrides |
| `memories` | Interaction summaries and narrative context |
| `agent_audit` | Before/after audit trail |
| `approvals` | Queued and interrupt-mode human approvals |
| `voice_calls` | ElevenLabs call records and transcripts |
| `checkpoints`, `checkpoint_writes` | LangGraph `MongoDBSaver` state |

## 3. Vector Search index

Continuum stores an `embedding` on memories when an embedding model is
configured, then queries them through LangChain's `MongoDBAtlasVectorSearch`
(`memory_vector_store()` and `patient_memory_retriever()` in
`backend/app/agent/memory.py`), which runs `$vectorSearch` pre-filtered by
`patient_id`. Hits are then re-ranked by importance and recency. The index must
therefore contain both a vector field and a filter field.

Index name: `memories_vector` (or whatever `MONGODB_VECTOR_INDEX` is set to).

Database: `continuum`  
Collection: `memories`  
Type: Atlas Vector Search

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 1536,
      "similarity": "cosine"
    },
    {
      "type": "filter",
      "path": "patient_id"
    }
  ]
}
```

`numDimensions` must match the embedding model in use. The app creates this
index itself on startup (as `type: vectorSearch`), embeds any memories that
lack vectors, and rebuilds the index if the model's dimensions change:

| Embeddings | Dimensions |
| --- | --- |
| `text-embedding-3-small` (OpenAI-compatible endpoint) | 1536 (`EMBEDDING_DIMENSIONS`) |
| Built-in local model `BAAI/bge-small-en-v1.5` (`EMBEDDING_PROVIDER=local`, or `auto` when the endpoint has no `/embeddings`) | 384 |

`/api/health` reports `"vector_search": true` once Atlas marks the index
queryable, and `"embeddings"` shows which model is active.

### Create it in the Atlas UI

1. Open your cluster → **Atlas Search** → **Create Search Index**.
2. Choose **Atlas Vector Search** and the JSON editor.
3. Select database `continuum`, collection `memories`, index name
   `memories_vector`.
4. Paste the JSON above and create the index.
5. Wait for the index status to become **Ready**.

### Or create it from `mongosh`

```javascript
db.getSiblingDB("continuum").runCommand({
  createSearchIndexes: "memories",
  indexes: [
    {
      name: "memories_vector",
      definition: {
        fields: [
          {
            type: "vector",
            path: "embedding",
            numDimensions: 1536,
            similarity: "cosine"
          },
          { type: "filter", path: "patient_id" }
        ]
      }
    }
  ]
});
```

The app also attempts this on startup (`ensure_vector_index()`). Some Atlas
tiers or database roles do not permit index creation; the failure is logged and
non-fatal.

## 4. Verify

```bash
curl -s http://localhost:8000/api/health | jq
```

Relevant fields:

```json
{
  "database": "connected",
  "llm": true,
  "vector_search": true
}
```

`vector_search: true` means the API can see the named Search index. If it is
`false`, recall still works through the lexical fallback.

## 5. Embeddings and re-seeding

Embeddings require an embedding-capable provider key (`LLM_API_KEY` or
`OPENAI_API_KEY`). New memories written through `repository.add_memory()` get an
embedding automatically. The seed path embeds its initial memories too.

If you change `EMBEDDING_MODEL` or `EMBEDDING_DIMENSIONS`:

1. Recreate the Atlas index with the new dimension.
2. Reset the demo data so memories are re-embedded:

```bash
curl -X POST 'http://localhost:8000/api/demo/seed?reset=true'
```

or replay Day 0 from the dashboard, which resets Michael and re-extracts the
discharge summary.

With no embedding key, memories have no `embedding` field and
`semantic_recall()` intentionally falls back to lexical scoring. Structured
questions ("what is overdue?") always use Mongo queries, not vector search.

## 6. Checkpoints

`MongoDBSaver` writes to `checkpoints` and `checkpoint_writes`. A paused
human-in-the-loop run resumes from the exact node after an approval. To clear a
single patient thread:

```javascript
db.checkpoints.deleteMany({ thread_id: "pat_michael" });
db.checkpoint_writes.deleteMany({ thread_id: "pat_michael" });
```

Day 0 replay performs this cleanup automatically.
