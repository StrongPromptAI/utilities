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
from .classify import classify_source, classify_batch
from .pretag import pretag_and_scrub, classify_backend_required
from .questions import extract_question_taxonomy

__all__ = [
    "create_ingest_source",
    "get_ingest_source",
    "get_ingest_source_by_file",
    "list_ingest_sources",
    "update_classification",
    "ingest_stats",
    "insert_ingest_chunks",
    "classify_source",
    "classify_batch",
    "extract_question_taxonomy",
]
