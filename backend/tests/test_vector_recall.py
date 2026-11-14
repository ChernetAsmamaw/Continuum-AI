"""Vector recall goes through LangChain's MongoDBAtlasVectorSearch."""

from datetime import datetime, timezone
from types import SimpleNamespace

from langchain_core.embeddings import DeterministicFakeEmbedding
from pymongo import MongoClient

from app.agent import memory
from app.config import settings


class CapturingCollection:
    """Stands in for an Atlas collection: records the pipeline, returns hits."""

    def __init__(self, hits):
        self.hits = hits
        self.pipelines = []
        self.database = SimpleNamespace(client=MongoClient(connect=False))

    def aggregate(self, pipeline):
        self.pipelines.append(pipeline)
        return iter([dict(h) for h in self.hits])


def test_semantic_recall_uses_langchain_vector_store(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    hits = [
        {"_id": "a", "id": "mem_a", "patient_id": "pat_x", "summary": "Cardiology booked Nov 25",
         "content": "Clinic confirmed cardiology for Nov 25.", "importance": 0.9, "created_at": now,
         "tags": ["clinic"], "score": 0.91},
        {"_id": "b", "id": "mem_b", "patient_id": "pat_x", "summary": "Weather chat",
         "content": "Talked about the weather.", "importance": 0.2, "created_at": now,
         "tags": [], "score": 0.40},
    ]
    collection = CapturingCollection(hits)
    monkeypatch.setattr(settings, "continuum_db_mode", "atlas")
    monkeypatch.setattr(memory, "get_embeddings", lambda: DeterministicFakeEmbedding(size=8))

    results = memory.semantic_recall(SimpleNamespace(memories=collection), "pat_x", "when is cardiology?", k=2)

    stage = collection.pipelines[0][0]["$vectorSearch"]
    assert stage["index"] == settings.mongodb_vector_index
    assert stage["path"] == "embedding"
    assert stage["filter"] == {"patient_id": {"$eq": "pat_x"}}
    assert [r["id"] for r in results] == ["mem_a", "mem_b"]
    assert all(r["retrieval"] == "atlas_vector" and "_id" not in r for r in results)
    assert results[0]["content"].startswith("Clinic confirmed")


def test_memory_mode_skips_vector_store():
    assert settings.is_memory_db
    assert memory.memory_vector_store(SimpleNamespace(memories=None)) is None
