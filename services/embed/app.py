"""
Embed service — nomic-embed-text-v1.5 ONNX embeddings.

Shared service for the shared-svcs Railway project.
Source: utilities/services/embed/

Endpoints:
  GET  /health              — 200 ready, 503 loading, 500 error (unauthenticated)
  POST /embed               — TEI-compatible: {"inputs": [str,...]} -> [[float,...],...]
  POST /v1/embeddings       — OpenAI-compatible (for OpenWebUI RAG)

Auth: JWT HS256, required claims: exp, iss, aud="embed". Bearer token in Authorization header.
No request body logging — inputs may contain patient text (PHI).
"""

import asyncio
import os
from typing import Union

import jwt as _jwt
from fastapi import Depends, FastAPI, HTTPException, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

app = FastAPI(title="embed")

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_SECRET_PREV = os.environ.get("JWT_SECRET_PREV")
_JWT_OPTIONS = {"require": ["exp", "iss", "aud"]}

_bearer = HTTPBearer()

_ready: bool = False
_load_error: str | None = None


def _require_embed_token(creds: HTTPAuthorizationCredentials = Security(_bearer)) -> None:
    """Validate Bearer JWT with aud=embed. Raises 401 on failure."""
    errors = []
    for secret in filter(None, [JWT_SECRET, JWT_SECRET_PREV]):
        try:
            _jwt.decode(creds.credentials, secret, algorithms=["HS256"], audience="embed", options=_JWT_OPTIONS)
            return
        except _jwt.PyJWTError as e:
            errors.append(e)
    raise HTTPException(status_code=401, detail="invalid token")


def _warmup() -> None:
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


class OpenAIEmbedRequest(BaseModel):
    input: Union[str, list[str]]
    model: str = "nomic-embed-text-v1.5"


@app.post("/embed", dependencies=[Depends(_require_embed_token)])
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


@app.post("/v1/embeddings", dependencies=[Depends(_require_embed_token)])
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
