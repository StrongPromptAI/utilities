"""Configuration constants for knowledge base.

LLM/embedding config lives in kb_config singleton table.
Static tuning parameters stay here.
"""

# Database
DB_URL = "postgresql://postgres:55@localhost:5433/knowledge_base"

# Chunking
DEFAULT_CHUNK_SIZE = 512
DEFAULT_OVERLAP = 50
TRANSCRIPT_TARGET_CHUNK_SIZE = 1000
BATCH_SIZE = 10  # chunks per batch (~5 min of conversation)

# Search
DEFAULT_DAYS_BACK = 21
DECAY_RATE = 0.95  # Per-day decay factor

# Quotes
QUOTES_PER_BATCH = 5  # Target quotes per batch extraction


def _load_singleton() -> dict:
    """Load kb_config singleton row. Cached after first call."""
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM kb_config WHERE id = 1")
            return dict(cur.fetchone())


_config = _load_singleton()

# LM Studio (for LLM inference)
LM_STUDIO_URL = _config["llm_url"]
SUMMARY_MODEL = _config["llm_model"]

# Embedding (sentence-transformers, local)
EMBED_MODEL = _config["embed_model"]
EMBED_BACKEND = _config["embed_backend"]
