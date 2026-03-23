"""Configuration constants for knowledge base.

LLM/embedding config lives in kb_config singleton table.
Static tuning parameters stay here.
"""
import json
import os
import subprocess

# Railway IDs for KB project (from CLAUDE.md)
_KB_PROJECT_ID = "a3677be5-5392-473e-b609-f23b7c06b78c"
_KB_ENV_ID = "3317309b-8f0c-43f4-9d8a-73b1c9fecf9c"
_KB_POSTGRES_SERVICE_ID = "ae33aa6f-3890-4af7-aec6-13904be1c242"


def _get_railway_db_url() -> str:
    """Fetch DATABASE_PUBLIC_URL from Railway GraphQL API."""
    keys_path = os.path.expanduser("~/.config/keys.json")
    token = json.load(open(keys_path))["railway"]
    query = (
        '{"query":"query { variables(projectId: \\"'
        + _KB_PROJECT_ID
        + '\\", environmentId: \\"'
        + _KB_ENV_ID
        + '\\", serviceId: \\"'
        + _KB_POSTGRES_SERVICE_ID
        + '\\") }"}'
    )
    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            "https://backboard.railway.com/graphql/v2",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Content-Type: application/json",
            "-d", query,
        ],
        capture_output=True, text=True, timeout=10,
    )
    data = json.loads(result.stdout)
    return data["data"]["variables"]["DATABASE_PUBLIC_URL"]


# Database — Railway Postgres (KB project)
# KB_DATABASE_URL overrides for testing/local dev
DB_URL = os.environ.get("KB_DATABASE_URL") or _get_railway_db_url()

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


def _load_singleton() -> dict | None:
    """Load kb_config singleton row. Returns None if DB unavailable."""
    import psycopg
    from psycopg.rows import dict_row
    try:
        with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM kb_config WHERE id = 1")
                return dict(cur.fetchone())
    except psycopg.OperationalError:
        return None


_config = _load_singleton()

# LM Studio (for LLM inference) — only available with local DB + LM Studio
LM_STUDIO_URL = _config["llm_url"] if _config else ""
SUMMARY_MODEL = _config["llm_model"] if _config else ""

# LLM context length (set via LM Studio SDK at pipeline start)
LLM_CONTEXT_LENGTH = 32768

# Embedding (sentence-transformers, local)
EMBED_MODEL = _config["embed_model"] if _config else "nomic-ai/nomic-embed-text-v1.5"
EMBED_BACKEND = _config["embed_backend"] if _config else "onnx"


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
