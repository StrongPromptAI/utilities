"""KB Ingest — source material analysis for chat agent requirements."""

from .crud import (
    create_ingest_source,
    get_ingest_source,
    get_ingest_source_by_file,
    list_ingest_sources,
    update_classification,
    ingest_stats,
    insert_ingest_chunks,
)
__all__ = [
    "create_ingest_source",
    "get_ingest_source",
    "get_ingest_source_by_file",
    "list_ingest_sources",
    "update_classification",
    "ingest_stats",
    "insert_ingest_chunks",
]
