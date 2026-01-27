"""Configuration constants for knowledge base."""

# Database
DB_URL = "postgresql://localhost/knowledge_base"

# LM Studio
LM_STUDIO_URL = "http://localhost:1234/v1"
EMBED_MODEL = "nomic-embed-text"
SUMMARY_MODEL = "qwen3-vl-8b-instruct-mlx"

# Chunking
DEFAULT_CHUNK_SIZE = 512
DEFAULT_OVERLAP = 50
TRANSCRIPT_TARGET_CHUNK_SIZE = 1000
BATCH_SIZE = 10  # chunks per batch (~5 min of conversation)

# Search
DEFAULT_DAYS_BACK = 21
DECAY_RATE = 0.95  # Per-day decay factor
