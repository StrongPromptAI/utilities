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

# Memory-appetite caps for the ONNX CPU runtime. Both default OFF because this is
# the small, always-on box and a heavy/bursty consumer (e.g. a bulk radar reindex
# pointed at the interactive service instead of embed-batch) must NOT be able to
# OOM it:
#   - CPU mem arena: ORT's arena pre-allocates and *retains* large blocks; with this
#     model's dynamic batch/seq shapes it keeps extending and never gives the memory
#     back, so RSS creeps up across requests until the container is OOM-killed (the
#     observed `Killed` crash-loop). Disabled, memory is released after each run —
#     small latency cost, stable RSS. This is the "don't eat like a goldfish" knob.
#   - mem pattern: assumes STATIC shapes; with our dynamic shapes ORT recommends
#     disabling it (it only wastes memory here).
# The fat batch box (8 GB) can re-enable either via env if it wants the throughput.
_CPU_MEM_ARENA = os.environ.get("EMBED_CPU_MEM_ARENA", "0") == "1"
_MEM_PATTERN = os.environ.get("EMBED_MEM_PATTERN", "0") == "1"


@lru_cache(maxsize=1)
def _get_session():
    """Load ONNX session + tokenizer from baked image paths, once per process.

    No `model_id` parameter on purpose. This is a single-model service, so there
    is exactly ONE lru_cache key and the model loads once. A defaulted
    `model_id=None` arg here was the OOM bug: `_get_session()` (key `()`) and
    `_get_session(None)` (key `(None,)`) hash to different cache keys, so the
    warmup path double-loaded the model every boot — peak ≈ 2× the model, which
    eats a 3 GB chat box. (Same lru_cache key-mismatch class as the in-process
    regression fixed in 02203d7, reincarnated in the sidecar.)

    Prod/staging: baked paths are required — missing files raise RuntimeError
    (never silently re-download, which can OOM concurrent with ORT init).
    Dev: falls back to HF download so fresh clones work with no setup.
    """
    model_id = _MODEL_ID
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
    sess_opts.enable_cpu_mem_arena = _CPU_MEM_ARENA
    sess_opts.enable_mem_pattern = _MEM_PATTERN
    session = ort.InferenceSession(
        model_path, sess_options=sess_opts, providers=["CPUExecutionProvider"]
    )
    logger.info(
        f"ONNX embedding model loaded (intra_op_threads={_INTRA_OP_THREADS}, "
        f"cpu_mem_arena={_CPU_MEM_ARENA}, mem_pattern={_MEM_PATTERN})"
    )
    return tokenizer, session


def _embed(texts: list[str]) -> np.ndarray:
    """Encode texts to normalized 768-dim embeddings."""
    tokenizer, session = _get_session()

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


async def generate_embedding(text: str) -> list[float]:
    """Generate embedding for a single text."""
    result = _embed([text])
    return result[0].tolist()


async def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts."""
    if not texts:
        return []
    result = _embed(texts)
    return [emb.tolist() for emb in result]
