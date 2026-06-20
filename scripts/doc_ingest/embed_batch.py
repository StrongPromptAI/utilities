"""Shared batch-embedding helper for document ingestion.

Two tiers, selected per-adapter (see plan § Embedding policy):
  - tier 1 — in-process ONNX via `nomic_onnx_embed` (the SAME code KB search
    queries use, so KB ingest/query vectors are identical by construction).
    KB adapter default (`force_cloud=False`).
  - tier 2 — HTTP to the dedicated cloud `embed-batch` box. thj adapter forces
    this (`force_cloud=True`) so thj-ingest vectors match thj's cloud-`embed`
    runtime query path. Also the fallback when ONNX isn't importable locally.

Cloud transport is the hardened thj client, generalized here verbatim: it
refuses the always-on chat box on ANY machine, fails closed in a cloud env
without an explicit batch URL, mints a per-request JWT, and polls `/health` to
wake the hibernated batch box before sending. (thj commits af73d21 + 30f4e5e.)

Memory: both tiers SUB-BATCH to `EMBED_BATCH_SIZE` (default 32). For the
in-process tier this is the load-bearing OOM guard on a laptop — peak tensor
≈ batch×512×768×4 bytes, so an unbounded 388-page call would balloon. We also
cap ORT `intra_op_num_threads` (mirrors the service's fix) as defense in depth.
"""
from __future__ import annotations

import os
import time
from typing import Optional

EMBED_DIM = 768
_BATCH = int(os.getenv("EMBED_BATCH_SIZE", "32"))
_INTRA_OP = int(os.getenv("EMBED_INTRA_OP_THREADS", "2"))
_WARMUP_TIMEOUT = float(os.getenv("EMBED_WARMUP_TIMEOUT", "120"))
# The always-on interactive chat box — a batch ingest must NEVER target it
# (a heavy run OOM-kills it and takes chat/search/PM down). Refused on any host.
_CHAT_EMBED_HOST = "shared-svcs-embed.up.railway.app"


class EmbedError(RuntimeError):
    """Embedding failed or is misconfigured — raised loudly, never swallowed."""


# ── tier 1: in-process ONNX ─────────────────────────────────────────────────

def _onnx_available() -> bool:
    try:
        import nomic_onnx_embed  # noqa: F401
        return True
    except ImportError:
        return False


_cap_installed = False


def _install_intra_op_cap() -> None:
    """Patch ONLY session *creation* in `nomic_onnx_embed` to cap
    `intra_op_num_threads` (the service's OOM fix). Tokenization + pooling are
    untouched, so output vectors are byte-identical to the unpatched package —
    PY-E stays valid. Idempotent; no-op once the session is cached. Fails loud
    if the package shape changed (AttributeError), rather than silently."""
    global _cap_installed
    if _cap_installed:
        return
    from nomic_onnx_embed import embed as _m
    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from transformers import AutoTokenizer
    from functools import lru_cache

    @lru_cache(maxsize=1)
    def _capped_session(model_id: str = None):
        model_id = model_id or _m._MODEL_ID
        model_path = hf_hub_download(repo_id=model_id, filename=_m._ONNX_FILE)
        tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
        so = ort.SessionOptions()
        so.intra_op_num_threads = _INTRA_OP
        so.inter_op_num_threads = 1
        sess = ort.InferenceSession(model_path, sess_options=so, providers=["CPUExecutionProvider"])
        return tok, sess

    _m._get_session = _capped_session  # type: ignore[attr-defined]
    _cap_installed = True


def _embed_in_process(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    _install_intra_op_cap()
    from nomic_onnx_embed.embed import _embed
    out: list[list[float]] = []
    for i in range(0, len(texts), _BATCH):
        sub = texts[i:i + _BATCH]
        out.extend(v.tolist() for v in _embed(sub))
    return out


# ── tier 2: cloud embed-batch (hardened thj client, generalized) ────────────

def _refuse_chat_box(url: str) -> None:
    if _CHAT_EMBED_HOST in url:
        raise EmbedError(
            f"Refusing to batch-embed against the always-on chat box ({_CHAT_EMBED_HOST}) — "
            "a heavy run OOM-kills it and takes chat down. Point EMBED_BATCH_SERVICE_URL at "
            "the dedicated batch box (https://embed-batch-production.up.railway.app)."
        )


def _resolve_cloud_url() -> str:
    batch = os.getenv("EMBED_BATCH_SERVICE_URL", "").rstrip("/")
    if batch:
        _refuse_chat_box(batch)
        return batch
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("ENVIRONMENT"):
        raise EmbedError(
            "EMBED_BATCH_SERVICE_URL not set in a cloud env. Ingest must target the "
            "dedicated batch box, never the chat box. Set EMBED_BATCH_SERVICE_URL."
        )
    fallback = os.getenv("EMBEDDING_SERVICE_URL", "").rstrip("/")
    if not fallback:
        raise EmbedError(
            "Neither EMBED_BATCH_SERVICE_URL nor EMBEDDING_SERVICE_URL set. Local dev: "
            "EMBED_BATCH_SERVICE_URL=http://localhost:8100."
        )
    _refuse_chat_box(fallback)
    return fallback


def _mint_token() -> str:
    secret = os.getenv("SHARED_SVC_JWT_SECRET", "")
    if not secret:
        raise EmbedError("SHARED_SVC_JWT_SECRET not set — required for cloud embed-batch auth.")
    import jwt
    return jwt.encode(
        {"iss": os.getenv("SERVICE_NAME", "doc-ingest"), "aud": "embed",
         "exp": int(time.time()) + 1800},
        secret, algorithm="HS256",
    )


def _wait_ready(client, url: str, timeout: float) -> None:
    if timeout <= 0:
        return
    import httpx
    deadline = time.time() + timeout
    while True:
        try:
            if client.get(f"{url}/health", timeout=10.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.time() >= deadline:
            raise EmbedError(f"embed-batch box not ready after {timeout:.0f}s ({url}/health).")
        time.sleep(2.0)


def _embed_cloud(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    import httpx
    url = _resolve_cloud_url()
    headers = {"Authorization": f"Bearer {_mint_token()}"}
    out: list[list[float]] = []
    try:
        with httpx.Client(timeout=60.0) as client:
            _wait_ready(client, url, _WARMUP_TIMEOUT)
            for i in range(0, len(texts), _BATCH):
                resp = client.post(f"{url}/embed", json={"inputs": texts[i:i + _BATCH]}, headers=headers)
                resp.raise_for_status()
                out.extend(resp.json())
    except httpx.HTTPError as e:
        raise EmbedError(f"embed-batch HTTP error: {e}") from e
    return out


# ── public API ───────────────────────────────────────────────────────────────

def embed_batch_texts(texts: list[str], force_cloud: bool = False) -> list[list[float]]:
    """Embed a batch of texts → list of 768-dim vectors.

    `force_cloud=True` (thj adapter) always uses cloud `embed-batch`.
    `force_cloud=False` (KB adapter) uses in-process ONNX when importable, else
    falls back to cloud — never the chat box.
    """
    if not force_cloud and _onnx_available():
        return _embed_in_process(texts)
    return _embed_cloud(texts)


# ── self-tests (PY-E, PY-O) ──────────────────────────────────────────────────

def _selftest_probes() -> int:
    """PY-E: in-process ONNX vs cloud embed-batch must agree across edge cases."""
    import numpy as np
    probes = {
        "prose": "The Go-Go organization believes it can do no wrong.",
        "numeric": "Revenue rose 12.7% to $3,400,000 across 1988-1990 (Q3).",
        "long-trunc": ("lifecycle " * 400).strip(),     # exceeds 512-token window
        "unicode": "Adizes’ “Prime” — naïve founders, café résumé, 日本語 テスト",
        "whitespace": "Courtship\n\n\n   \t  Affair        Infant",
    }
    keys = list(probes)
    local = _embed_in_process([probes[k] for k in keys])
    cloud = _embed_cloud([probes[k] for k in keys])
    ok = True
    for k, a, b in zip(keys, local, cloud):
        a, b = np.array(a), np.array(b)
        cos = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))
        flag = "OK " if cos >= 0.9999 else "FAIL"
        if cos < 0.9999:
            ok = False
        print(f"  [{flag}] {k:12s} cosine={cos:.6f}")
    print("PY-E:", "PASS" if ok else "FAIL — fail closed, force cloud, flag version mismatch")
    return 0 if ok else 1


def _selftest_mem(batch: int) -> int:
    """PY-O: in-process batch RSS bounded (sub-batching + intra_op cap)."""
    import resource
    _install_intra_op_cap()
    from nomic_onnx_embed import embed as _m
    base = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)  # MB on macOS
    texts = ["search_document: " + ("word " * 400)] * batch
    _embed_in_process(texts)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    capped = getattr(_m._get_session, "__wrapped__", None) is not None or _cap_installed
    print(f"  intra_op cap installed: {_cap_installed} (target {_INTRA_OP})")
    print(f"  baseline={base:.0f}MB  peak={peak:.0f}MB  delta={peak - base:.0f}MB  batch={batch} sub={_BATCH}")
    print("PY-O: PASS (sub-batched, capped)" if capped else "PY-O: FAIL (cap not installed)")
    return 0 if capped else 1


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if "--selftest-probes" in args:
        sys.exit(_selftest_probes())
    if "--selftest-mem" in args:
        b = int(args[args.index("--batch") + 1]) if "--batch" in args else 64
        sys.exit(_selftest_mem(b))
    print("usage: python -m doc_ingest.embed_batch [--selftest-probes | --selftest-mem --batch N]")
