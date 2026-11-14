"""LangGraph checkpointer factory.

Uses the official MongoDB saver so an interrupted run resumes exactly where it
stopped. Falls back to an in-process saver when running with mongomock.
"""

from __future__ import annotations

import logging

from ..config import settings
from ..db import get_client

log = logging.getLogger("continuum.checkpoint")

_saver = None


def get_checkpointer():
    global _saver
    if _saver is not None:
        return _saver

    if settings.is_memory_db:
        from langgraph.checkpoint.memory import MemorySaver

        _saver = MemorySaver()
        return _saver

    try:
        from langgraph.checkpoint.mongodb import MongoDBSaver

        _saver = MongoDBSaver(
            get_client(),
            db_name=settings.mongodb_db,
            checkpoint_collection_name="checkpoints",
            writes_collection_name="checkpoint_writes",
        )
        log.info("LangGraph MongoDBSaver ready (db=%s).", settings.mongodb_db)
        return _saver
    except Exception as exc:  # pragma: no cover - dependency/environment dependent
        log.warning("MongoDBSaver unavailable (%s); falling back to MemorySaver.", exc)
        from langgraph.checkpoint.memory import MemorySaver

        _saver = MemorySaver()
        return _saver
