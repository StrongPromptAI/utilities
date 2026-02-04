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

# CRUD - Clients
from .crud.clients import (
    get_client,
    list_clients,
    create_client,
    get_or_create_client,
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
    get_calls_for_client,
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

# CRUD - Participants
from .crud.participants import (
    add_participant,
    add_participants,
    get_call_participants,
    get_calls_by_participant,
)

# Search
from .search import (
    semantic_search,
    hybrid_search,
    semantic_search_with_fallback,
    get_client_context,
)

# Analysis
from .analysis import suggested_next_step

# Quotes
from .quotes import (
    extract_quotes_from_batch,
    extract_call_quotes,
    deduplicate_quotes,
    draft_letter,
)

# CRUD - Quotes
from .crud.quotes import (
    insert_candidate_quotes,
    get_candidate_quotes,
    get_approved_quotes,
    approve_quote,
    reject_quote,
    bulk_approve_quotes,
    bulk_reject_quotes,
    clear_candidate_quotes,
)

# CRUD - Decisions
from .crud.decisions import (
    create_decision,
    get_decision,
    list_decisions,
    update_decision_status,
    clear_candidate_decisions,
    insert_candidate_decisions,
    get_candidate_decisions,
    confirm_decision,
    reject_decision,
)

# CRUD - Open Questions
from .crud.open_questions import (
    create_open_question,
    get_open_question,
    list_open_questions,
    resolve_question,
    clear_candidate_questions,
    insert_candidate_questions,
    get_candidate_questions,
    abandon_question,
)

# Harvest
from .harvest import (
    harvest_from_summaries,
    harvest_call,
    deduplicate_harvest,
)

# Clustering
from .clustering import (
    compute_clusters,
    store_clusters,
    get_cluster_details,
    expand_by_cluster,
)

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
    # Clients
    "get_client",
    "list_clients",
    "create_client",
    "get_or_create_client",
    # Projects
    "get_project",
    "list_projects",
    "get_project_docs",
    # Calls
    "get_call_by_source_file",
    "delete_call",
    "create_call",
    "get_calls_for_client",
    "update_call_summary",
    # Chunks
    "insert_chunks",
    "get_call_chunks",
    "summarize_chunk_batch",
    "generate_call_batch_summaries",
    "get_call_batch_summaries",
    "get_call_summary_text",
    # Participants
    "add_participant",
    "add_participants",
    "get_call_participants",
    "get_calls_by_participant",
    # Search
    "semantic_search",
    "hybrid_search",
    "semantic_search_with_fallback",
    "get_client_context",
    # Analysis
    "suggested_next_step",
    # Quotes
    "extract_quotes_from_batch",
    "extract_call_quotes",
    "deduplicate_quotes",
    "draft_letter",
    "insert_candidate_quotes",
    "get_candidate_quotes",
    "get_approved_quotes",
    "approve_quote",
    "reject_quote",
    "bulk_approve_quotes",
    "bulk_reject_quotes",
    "clear_candidate_quotes",
    # Decisions
    "create_decision",
    "get_decision",
    "list_decisions",
    "update_decision_status",
    "clear_candidate_decisions",
    "insert_candidate_decisions",
    "get_candidate_decisions",
    "confirm_decision",
    "reject_decision",
    # Open Questions
    "create_open_question",
    "get_open_question",
    "list_open_questions",
    "resolve_question",
    "clear_candidate_questions",
    "insert_candidate_questions",
    "get_candidate_questions",
    "abandon_question",
    # Harvest
    "harvest_from_summaries",
    "harvest_call",
    "deduplicate_harvest",
    # Clustering
    "compute_clusters",
    "store_clusters",
    "get_cluster_details",
    "expand_by_cluster",
]
