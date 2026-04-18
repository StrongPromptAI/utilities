"""
Embed service — nomic-embed-text-v1.5 ONNX embeddings.

Shared service deployed per-project for PHI isolation.
Source: utilities/services/embed/

Endpoints:
  GET  /health              — 200 ready, 503 loading, 500 error
  POST /embed               — TEI-compatible: {"inputs": [str,...]} -> [[float,...],...]
  POST /v1/embeddings       — OpenAI-compatible (for OpenWebUI RAG)

No request body logging — inputs may contain patient text (PHI).
"""

import asyncio
from typing import Union

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

app = FastAPI(title="embed")

_ready: bool = False
_load_error: str | None = None


_WARMUP_TIMEOUT = 120  # seconds — loading a 1.5 GB ONNX model from cache takes ~30-60 s


def _warmup() -> None:
    """Load model and run one inference to confirm it works."""
    global _ready, _load_error
    try:
        from nomic_embed import _get_session, _embed
        _get_session()
        result = _embed(["warmup"])
        assert result.shape[1] == 768, f"Expected 768-dim, got {result.shape[1]}"
        _ready = True
        print("[embed] Ready (768-dim, warm-up passed)")
    except Exception as exc:
        _load_error = str(exc)
        print(f"[embed] Load failed: {exc}")


@app.on_event("startup")
async def startup() -> None:
    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, _warmup),
            timeout=_WARMUP_TIMEOUT,
        )
    except asyncio.TimeoutError:
        global _load_error
        _load_error = (
            f"Model warmup timed out after {_WARMUP_TIMEOUT}s — "
            "the ONNX model may not be cached in the image. "
            "Check that HF_HOME is set consistently in the Dockerfile and at runtime."
        )
        print(f"[embed] {_load_error}")


@app.get("/health")
def health() -> Response:
    if _load_error:
        return Response(content=_load_error, status_code=500, media_type="text/plain")
    if not _ready:
        return Response(content="loading", status_code=503, media_type="text/plain")
    return Response(
        content='{"status":"ok","model":"embed","dims":768}',
        status_code=200,
        media_type="application/json",
    )


class EmbedRequest(BaseModel):
    inputs: Union[str, list[str]]


class OpenAIEmbedRequest(BaseModel):
    input: Union[str, list[str]]
    model: str = "nomic-embed-text-v1.5"


@app.post("/embed")
async def embed(req: EmbedRequest):
    """TEI-compatible batch embedding."""
    if not _ready:
        raise HTTPException(503, "Embedding model loading")

    texts = [req.inputs] if isinstance(req.inputs, str) else req.inputs
    if not texts:
        raise HTTPException(400, "inputs is required")

    from nomic_embed import _embed

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: _embed(texts))
        return result.tolist()
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.post("/v1/embeddings")
async def openai_embeddings(req: OpenAIEmbedRequest):
    """OpenAI-compatible embeddings endpoint for OpenWebUI RAG."""
    if not _ready:
        raise HTTPException(503, "Embedding model loading")

    texts = [req.input] if isinstance(req.input, str) else req.input
    if not texts:
        raise HTTPException(400, "input is required")

    from nomic_embed import _embed

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: _embed(texts))
        vectors = result.tolist()
    except Exception as exc:
        raise HTTPException(500, str(exc))

    return {
        "object": "list",
        "model": req.model,
        "data": [
            {"object": "embedding", "index": i, "embedding": vec}
            for i, vec in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
