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

# LLM context length (set via LM Studio SDK at pipeline start)
LLM_CONTEXT_LENGTH = 32768

# Embedding (sentence-transformers, local)
EMBED_MODEL = _config["embed_model"]
EMBED_BACKEND = _config["embed_backend"]


def ensure_model():
    """Ensure LM Studio has the model loaded with correct context length.

    Call once at pipeline start (harvest, harvest-review), not per LLM call.
    Idempotent — skips if model already loaded with sufficient context.
    """
    import lmstudio as lms

    loaded = lms.list_loaded_models("llm")
    for m in loaded:
        if SUMMARY_MODEL in str(m):
            ctx = m.get_context_length()
            if ctx >= LLM_CONTEXT_LENGTH:
                return  # Already good
            print(f"  Model loaded with {ctx} context, need {LLM_CONTEXT_LENGTH}. Reloading...")
            break

    lms.llm(SUMMARY_MODEL, config={"contextLength": LLM_CONTEXT_LENGTH})
