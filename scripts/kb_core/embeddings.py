"""Embedding generation via LM Studio."""

from openai import OpenAI
from .config import LM_STUDIO_URL, EMBED_MODEL


def get_embedding(text: str) -> list[float]:
    """Generate embedding via LM Studio."""
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    response = client.embeddings.create(model=EMBED_MODEL, input=text)
    return response.data[0].embedding
