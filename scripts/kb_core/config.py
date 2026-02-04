"""Configuration constants for knowledge base."""

# Database
DB_URL = "postgresql://postgres:55@localhost:5433/knowledge_base"

# LM Studio (for LLM inference)
LM_STUDIO_URL = "http://localhost:1234/v1"

# Embedding (sentence-transformers, local)
EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"
SUMMARY_MODEL = "mistral-small-3.2-24b-instruct-2506"

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
