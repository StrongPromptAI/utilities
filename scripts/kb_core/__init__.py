"""
Knowledge Base Core Library

Shared functions for kb-ingest and kb-check skills.
"""

# Config
from .config import (
    DB_URL,
    LM_STUDIO_URL,
    EMBED_MODEL,
    EMBED_BACKEND,
    SUMMARY_MODEL,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    TRANSCRIPT_TARGET_CHUNK_SIZE,
    BATCH_SIZE,
    DEFAULT_DAYS_BACK,
    DECAY_RATE,
    QUOTES_PER_BATCH,
    LLM_CONTEXT_LENGTH,
    ensure_model,
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
    get_calls_for_org,
    update_call_summary,
    update_user_notes,
    list_calls,
    get_call_detail,
    get_call_context,
)

# CRUD - Actions
from .crud.actions import (
    create_action,
    list_actions,
    get_action,
    get_action_prompt_file,
    update_action_status,
    insert_candidate_actions,
    get_candidate_actions,
    clear_candidate_actions,
    confirm_action,
    reject_action,
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
    get_org_context,
)

# Analysis
from .analysis import suggested_next_step

# Quotes
from .quotes import (
    extract_quotes_from_batch,
    extract_call_quotes,
    rank_quotes,
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

# CRUD - Questions (unified: includes decisions)
from .crud.questions import (
    create_open_question,
    get_open_question,
    list_questions as list_open_questions,
    resolve_question,
    decide_question,
    get_decided_questions,
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
    build_harvest_review,
)

# Synthesis
from .synthesis import (
    synthesize_call,
    synthesize_project,
    type_to_slug,
    apply_additions,
    _build_seed_template,
)

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
    "LM_STUDIO_URL",
    "EMBED_MODEL",
    "EMBED_BACKEND",
    "SUMMARY_MODEL",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_OVERLAP",
    "TRANSCRIPT_TARGET_CHUNK_SIZE",
    "BATCH_SIZE",
    "DEFAULT_DAYS_BACK",
    "DECAY_RATE",
    "QUOTES_PER_BATCH",
    "LLM_CONTEXT_LENGTH",
    "ensure_model",
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
    "get_calls_for_org",
    "update_call_summary",
    "update_user_notes",
    "list_calls",
    "get_call_detail",
    "get_call_context",
    # Actions
    "create_action",
    "list_actions",
    "get_action",
    "get_action_prompt_file",
    "update_action_status",
    "insert_candidate_actions",
    "get_candidate_actions",
    "clear_candidate_actions",
    "confirm_action",
    "reject_action",
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
    "get_org_context",
    # Analysis
    "suggested_next_step",
    # Quotes
    "extract_quotes_from_batch",
    "extract_call_quotes",
    "rank_quotes",
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
    # Questions (unified: includes decisions)
    "create_open_question",
    "get_open_question",
    "list_open_questions",
    "resolve_question",
    "decide_question",
    "get_decided_questions",
    "clear_candidate_questions",
    "insert_candidate_questions",
    "get_candidate_questions",
    "abandon_question",
    # Harvest
    "harvest_from_summaries",
    "harvest_call",
    "deduplicate_harvest",
    "build_harvest_review",
    # Synthesis
    "synthesize_call",
    "synthesize_project",
    "type_to_slug",
    "apply_additions",
    "_build_seed_template",
    # Clustering
    "compute_clusters",
    "store_clusters",
    "get_cluster_details",
    "expand_by_cluster",
    "cluster_label",
]
