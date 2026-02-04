"""Embedding generation via sentence-transformers (local) or TEI (production)."""

import os
from functools import lru_cache

from .config import EMBED_MODEL


@lru_cache(maxsize=1)
def _get_model():
    """Load sentence-transformers model once, cache across calls."""
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL, trust_remote_code=True)


def get_embedding(text: str) -> list[float]:
    """Generate embedding using sentence-transformers locally."""
    model = _get_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()
