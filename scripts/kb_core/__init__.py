"""
Knowledge Base Core Library

Shared functions for kb-ingest and kb-check skills.
"""

# Config
from .config import (
    DB_URL,
    LM_STUDIO_URL,
    EMBED_MODEL,
    SUMMARY_MODEL,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    TRANSCRIPT_TARGET_CHUNK_SIZE,
    BATCH_SIZE,
    DEFAULT_DAYS_BACK,
    DECAY_RATE,
)

# Core utilities
from .db import get_db
from .embeddings import get_embedding

# Chunking
from .chunking import (
    chunk_text,
    chunk_by_sections,
    chunk_transcript,
)

# Transcripts
from .transcripts import preprocess_dialpad_transcript

# CRUD - Stakeholders
from .crud.stakeholders import (
    get_stakeholder,
    list_stakeholders,
    create_stakeholder,
    get_or_create_stakeholder,
)

# CRUD - Projects
from .crud.projects import (
    get_project,
    list_projects,
    get_project_docs,
)

# CRUD - Calls
from .crud.calls import (
    get_call_by_source_file,
    delete_call,
    create_call,
    get_calls_for_stakeholder,
    update_call_summary,
)

# CRUD - Chunks
from .crud.chunks import (
    insert_chunks,
    get_call_chunks,
    summarize_chunk_batch,
    generate_call_batch_summaries,
    get_call_batch_summaries,
    get_call_summary_text,
)

# Search
from .search import (
    semantic_search,
    hybrid_search,
    semantic_search_with_fallback,
    get_stakeholder_context,
)

# Analysis
from .analysis import suggested_next_step

__all__ = [
    # Config
    "DB_URL",
    "LM_STUDIO_URL",
    "EMBED_MODEL",
    "SUMMARY_MODEL",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_OVERLAP",
    "TRANSCRIPT_TARGET_CHUNK_SIZE",
    "BATCH_SIZE",
    "DEFAULT_DAYS_BACK",
    "DECAY_RATE",
    # Core
    "get_db",
    "get_embedding",
    # Chunking
    "chunk_text",
    "chunk_by_sections",
    "chunk_transcript",
    # Transcripts
    "preprocess_dialpad_transcript",
    # Stakeholders
    "get_stakeholder",
    "list_stakeholders",
    "create_stakeholder",
    "get_or_create_stakeholder",
    # Projects
    "get_project",
    "list_projects",
    "get_project_docs",
    # Calls
    "get_call_by_source_file",
    "delete_call",
    "create_call",
    "get_calls_for_stakeholder",
    "update_call_summary",
    # Chunks
    "insert_chunks",
    "get_call_chunks",
    "summarize_chunk_batch",
    "generate_call_batch_summaries",
    "get_call_batch_summaries",
    "get_call_summary_text",
    # Search
    "semantic_search",
    "hybrid_search",
    "semantic_search_with_fallback",
    "get_stakeholder_context",
    # Analysis
    "suggested_next_step",
]
