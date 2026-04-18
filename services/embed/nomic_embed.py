"""
Unified embedding service using ONNX Runtime directly.

Uses nomic-ai/nomic-embed-text-v1.5 (768-dim) with pre-exported ONNX weights.
No torch/optimum dependency — just onnxruntime + transformers tokenizer + numpy.
"""

import os
from functools import lru_cache
from pathlib import Path

# Set HF_HOME before importing huggingface_hub so both the build-time cache
# and the runtime loader resolve to the same directory.  The Dockerfile also
# sets this env var; the assignment here is a belt-and-suspenders guard for
# environments where the var might not be inherited.
os.environ.setdefault("HF_HOME", "/app/.cache/huggingface")

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from loguru import logger
from transformers import AutoTokenizer

_MODEL_ID = "nomic-ai/nomic-embed-text-v1.5"
_ONNX_FILE = "onnx/model.onnx"
_CACHE_DIR = Path(os.environ["HF_HOME"]) / "hub"


@lru_cache(maxsize=1)
def _get_session(model_id: str = None):
    """Load ONNX weights from cache (or download if missing) and create inference session."""
    model_id = model_id or _MODEL_ID

    # Determine whether the model file is already present in the baked-in cache.
    # hf_hub_download returns immediately when the file is already cached.
    cache_hit = any(_CACHE_DIR.glob(f"**/onnx/model.onnx")) if _CACHE_DIR.exists() else False
    if cache_hit:
        logger.info(f"Loading ONNX model from cache: {model_id} (HF_HOME={os.environ['HF_HOME']})")
    else:
        logger.warning(
            f"Model not found in cache at {_CACHE_DIR} — downloading {model_id}. "
            "This will be slow; ensure HF_HOME is set consistently between build and runtime."
        )

    model_path = hf_hub_download(repo_id=model_id, filename=_ONNX_FILE)
    logger.info(f"ONNX model path: {model_path}")
    tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

    session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    logger.info("ONNX embedding model loaded")
    return tokenizer, session


def _embed(texts: list[str], model_id: str = None) -> np.ndarray:
    """Encode texts to normalized 768-dim embeddings."""
    tokenizer, session = _get_session(model_id)

    inputs = tokenizer(texts, padding=True, truncation=True, max_length=512, return_tensors="np")

    # Run ONNX inference
    ort_inputs = {
        "input_ids": inputs["input_ids"].astype(np.int64),
        "attention_mask": inputs["attention_mask"].astype(np.int64),
    }
    # Add token_type_ids if model expects it
    input_names = [inp.name for inp in session.get_inputs()]
    if "token_type_ids" in input_names and "token_type_ids" in inputs:
        ort_inputs["token_type_ids"] = inputs["token_type_ids"].astype(np.int64)

    outputs = session.run(None, ort_inputs)
    token_embeddings = outputs[0]  # (batch, seq_len, hidden_dim)

    # Mean pooling
    mask = inputs["attention_mask"].astype(np.float32)
    mask_expanded = np.expand_dims(mask, axis=-1)
    summed = np.sum(token_embeddings * mask_expanded, axis=1)
    counts = np.clip(np.sum(mask_expanded, axis=1), a_min=1e-9, a_max=None)
    pooled = summed / counts

    # L2 normalize
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    return pooled / np.clip(norms, a_min=1e-9, a_max=None)


async def generate_embedding(text: str, model_id: str = None) -> list[float]:
    """Generate embedding for a single text."""
    result = _embed([text], model_id)
    return result[0].tolist()


async def generate_embeddings(texts: list[str], model_id: str = None) -> list[list[float]]:
    """Generate embeddings for multiple texts."""
    if not texts:
        return []
    result = _embed(texts, model_id)
    return [emb.tolist() for emb in result]
