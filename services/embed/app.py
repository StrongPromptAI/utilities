"""
Embed service — nomic-embed-text-v1.5 ONNX embeddings.

Shared service deployed per-project for PHI isolation.
Source: utilities/services/embed/

Endpoints:
  GET  /health   — 200 ready, 503 loading, 500 error
  POST /embed    — TEI-compatible: {"inputs": [str,...]} -> [[float,...],...]

No request body logging — inputs may contain patient text (PHI).
"""

import asyncio
from typing import Union

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

app = FastAPI(title="embed")

_ready: bool = False
_load_error: str | None = None


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
    await loop.run_in_executor(None, _warmup)


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
