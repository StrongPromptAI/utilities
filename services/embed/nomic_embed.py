"""
Unified embedding service using ONNX Runtime directly.

Uses nomic-ai/nomic-embed-text-v1.5 (768-dim) with pre-exported ONNX weights.
No torch/optimum dependency — just onnxruntime + transformers tokenizer + numpy.
"""

import os
from functools import lru_cache

import numpy as np
import onnxruntime as ort
from loguru import logger
from transformers import AutoTokenizer

_MODEL_ID = "nomic-ai/nomic-embed-text-v1.5"
_ONNX_FILE = "onnx/model.onnx"
_LOCAL_MODEL_PATH = "/app/models/nomic/onnx/model.onnx"
_LOCAL_TOKENIZER_DIR = "/app/models/tokenizer"

_IS_PROD = (
    os.environ.get("ENVIRONMENT") or os.environ.get("RAILWAY_ENVIRONMENT") or ""
) in ("production", "staging")

# onnxruntime intra-op thread cap. Without it, ORT sizes its intra-op pool to the
# CPU grant (cpu=N on Railway → ~N threads, each with a memory arena), so a single
# batch can balloon to multiple GB and OOM-kill the container — the identical failure
# TTS hit and fixed (services/tts/app.py). The pip onnxruntime wheel is not OpenMP-built,
# so OMP_NUM_THREADS is ignored — SessionOptions.intra_op_num_threads is the only reliable
# knob. inter_op is pinned to 1 (single graph, serial). Default 2 suits the small always-on
# chat box; the fat batch box raises it via env to use its larger CPU grant.
_INTRA_OP_THREADS = int(os.environ.get("EMBED_INTRA_OP_THREADS", "2"))


@lru_cache(maxsize=1)
def _get_session(model_id: str = None):
    """Load ONNX session + tokenizer from baked image paths.

    Prod/staging: baked paths are required — missing files raise RuntimeError
    (never silently re-download, which can OOM concurrent with ORT init).
    Dev: falls back to HF download so fresh clones work with no setup.
    """
    model_id = model_id or _MODEL_ID
    logger.info(f"Loading ONNX model: {model_id}")

    baked_present = os.path.exists(_LOCAL_MODEL_PATH) and os.path.exists(_LOCAL_TOKENIZER_DIR)

    if baked_present:
        model_path = _LOCAL_MODEL_PATH
        tokenizer = AutoTokenizer.from_pretrained(_LOCAL_TOKENIZER_DIR)
    elif _IS_PROD:
        raise RuntimeError(
            f"Baked model missing in prod: expected {_LOCAL_MODEL_PATH} and {_LOCAL_TOKENIZER_DIR}. "
            "Fix the Dockerfile bake step — do not fall back to HF download (OOM risk)."
        )
    else:
        from huggingface_hub import hf_hub_download
        logger.warning("Dev mode: baked model not found, downloading from HF")
        model_path = hf_hub_download(repo_id=model_id, filename=_ONNX_FILE)
        tokenizer = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)

    sess_opts = ort.SessionOptions()
    sess_opts.intra_op_num_threads = _INTRA_OP_THREADS
    sess_opts.inter_op_num_threads = 1
    session = ort.InferenceSession(
        model_path, sess_options=sess_opts, providers=["CPUExecutionProvider"]
    )
    logger.info(f"ONNX embedding model loaded (intra_op_threads={_INTRA_OP_THREADS})")
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
