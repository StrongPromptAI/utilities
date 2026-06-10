"""
Knowledge Base Core Library

Shared functions for kb-ingest and kb-check skills.
"""

# Config
from .config import (
    DB_URL,
    EMBED_MODEL,
    EMBED_BACKEND,
    PRIMARY_LLM_URL,
    PRIMARY_LLM_MODEL,
    PRIMARY_LLM_PROVIDER,
    BACKUP_LLM_URL,
    BACKUP_LLM_MODEL,
    BACKUP_LLM_PROVIDER,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    TRANSCRIPT_TARGET_CHUNK_SIZE,
    BATCH_SIZE,
    DEFAULT_DAYS_BACK,
    DECAY_RATE,
    QUOTES_PER_BATCH,
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
from .transcripts import preprocess_transcript

# CRUD - Org
from .crud.org import (
    get_org,
    list_org,
    create_org,
    get_or_create_org,
)

# CRUD - Contacts
from .crud.contacts import (
    get_contact,
    get_contact_by_id,
    list_contacts,
    create_contact,
    get_or_create_contact,
    add_contacts_to_call,
    get_call_contacts,
    get_calls_by_contact,
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
    get_raw_transcript,
    get_calls_for_org,
    update_call_summary,
    update_user_notes,
    list_calls,
    get_call_detail,
    get_call_context,
    add_call_output,
    get_call_outputs,
)

# CRUD - Chunks
from .crud.chunks import (
    insert_chunks,
    get_call_chunks,
)

# Search
from .search import (
    semantic_search,
    hybrid_search,
    semantic_search_with_fallback,
    get_org_context,
)

# Summarize (plan 26-5-21)
from .summarize import (
    generate_summary,
    get_outline,
    get_summary,
    upsert_outline,
)

# Scrub (plan 26-5-21)
from .scrub import scrub, rehydrate

# LLM dispatch (plan 26-5-21)
from .llm import complete_with_fallback

# Stub for downstream callers that still import suggested_next_step
def suggested_next_step(*args, **kwargs):
    raise NotImplementedError(
        "suggested_next_step was removed in plan 26-5-21. "
        "Use generate_summary() with an outline instead."
    )

# Transcription
from .transcribe import transcribe_audio

# Clustering
from .clustering import (
    compute_clusters,
    store_clusters,
    get_cluster_details,
    expand_by_cluster,
    cluster_label,
)

__all__ = [
    # Config
    "DB_URL",
    "EMBED_MODEL",
    "EMBED_BACKEND",
    "PRIMARY_LLM_URL",
    "PRIMARY_LLM_MODEL",
    "PRIMARY_LLM_PROVIDER",
    "BACKUP_LLM_URL",
    "BACKUP_LLM_MODEL",
    "BACKUP_LLM_PROVIDER",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_OVERLAP",
    "TRANSCRIPT_TARGET_CHUNK_SIZE",
    "BATCH_SIZE",
    "DEFAULT_DAYS_BACK",
    "DECAY_RATE",
    "QUOTES_PER_BATCH",
    # Core
    "get_db",
    "get_embedding",
    # Chunking
    "chunk_text",
    "chunk_by_sections",
    "chunk_transcript",
    # Transcripts
    "preprocess_transcript",
    # Org
    "get_org",
    "list_org",
    "create_org",
    "get_or_create_org",
    # Contacts
    "get_contact",
    "get_contact_by_id",
    "list_contacts",
    "create_contact",
    "get_or_create_contact",
    "add_contacts_to_call",
    "get_call_contacts",
    "get_calls_by_contact",
    # Projects
    "get_project",
    "list_projects",
    "get_project_docs",
    # Calls
    "get_call_by_source_file",
    "delete_call",
    "create_call",
    "get_raw_transcript",
    "get_calls_for_org",
    "update_call_summary",
    "update_user_notes",
    "list_calls",
    "get_call_detail",
    "get_call_context",
    "add_call_output",
    "get_call_outputs",
    # Chunks
    "insert_chunks",
    "get_call_chunks",
    # Search
    "semantic_search",
    "hybrid_search",
    "semantic_search_with_fallback",
    "get_org_context",
    # Summarize
    "generate_summary",
    "get_outline",
    "get_summary",
    "upsert_outline",
    # Scrub
    "scrub",
    "rehydrate",
    # LLM
    "complete_with_fallback",
    # Transcription
    "transcribe_audio",
    # Clustering
    "compute_clusters",
    "store_clusters",
    "get_cluster_details",
    "expand_by_cluster",
    "cluster_label",
]
