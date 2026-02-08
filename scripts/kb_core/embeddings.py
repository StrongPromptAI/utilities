"""Embedding generation via nomic-onnx-embed ONNX package.

Model ID sourced from kb_config singleton via config.py.
"""

from .config import EMBED_MODEL
from nomic_onnx_embed.embed import _embed


def get_embedding(text: str) -> list[float]:
    """Generate embedding for a single text (sync)."""
    result = _embed([text], model_id=EMBED_MODEL)
    return result[0].tolist()
