"""
Embed service — nomic-embed-text-v1.5 ONNX embeddings.

Shared service for the shared-svcs Railway project.
Source: utilities/services/embed/

Endpoints:
  GET  /health              — 200 ready, 503 loading, 500 error (always unauthenticated)
  GET  /mem                 — RSS snapshot (always unauthenticated, for monitoring)
  POST /embed               — TEI-compatible: {"inputs": [str,...]} -> [[float,...],...]
  POST /v1/embeddings       — OpenAI-compatible (for OpenWebUI RAG, gitnexus, etc.)

Auth: JWT HS256, required claims: exp, iss, aud="embed". Bearer token in Authorization header.
Enforced only in prod/staging (ENVIRONMENT or RAILWAY_ENVIRONMENT set). Dev mode
serves every endpoint unauthenticated — no local token setup required.

No request body logging — inputs may contain patient text (PHI).
"""

import asyncio
import gc
import os
from typing import Union

import jwt as _jwt
from fastapi import Depends, FastAPI, HTTPException, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

app = FastAPI(title="embed")

# Environment resolution: explicit ENVIRONMENT wins, else Railway's auto-set
# RAILWAY_ENVIRONMENT, else "development". Dev-mode disables auth so hooks,
# gitnexus, and local backends can call the service with no token setup.
ENVIRONMENT = (
    os.environ.get("ENVIRONMENT")
    or os.environ.get("RAILWAY_ENVIRONMENT")
    or "development"
)
_IS_PROD = ENVIRONMENT in ("production", "staging")

if _IS_PROD:
    JWT_SECRET = os.environ["JWT_SECRET"]
    JWT_SECRET_PREV = os.environ.get("JWT_SECRET_PREV")
else:
    # Dev defaults — arbitrary, never trusted on prod because _IS_PROD gates auth
    JWT_SECRET = os.environ.get("JWT_SECRET", "localdev")
    JWT_SECRET_PREV = None

_JWT_OPTIONS = {"require": ["exp", "iss", "aud"]}

_bearer = HTTPBearer()

# Per-request memory ceilings. EMBED_MAX_BATCH bounds the WIDTH of one request
# (peak memory scales batch × 512 × 768 × 4 bytes); EMBED_MAX_CONCURRENCY bounds
# how many inferences run at once. Together with the intra-op thread cap in
# nomic_embed.py, peak memory is a known fixed number the box is sized to. Defaults
# suit the small always-on chat box; the fat batch box raises both via env.
EMBED_MAX_BATCH = int(os.environ.get("EMBED_MAX_BATCH", "64"))
EMBED_MAX_CONCURRENCY = int(os.environ.get("EMBED_MAX_CONCURRENCY", "2"))

# Load-shedding queue depth. The semaphore caps how many inferences run at once
# (the memory ceiling); this caps how many may WAIT for it. Past
# EMBED_MAX_CONCURRENCY + EMBED_MAX_QUEUE in-flight, new requests get a fast 503 +
# Retry-After instead of joining an unbounded queue that turns a user spike into a
# hang for everyone. Clients (embed_client, gitnexus) already retry 503 with backoff,
# so the spike smooths out. Default = 4× concurrency; no need to tune per box.
EMBED_MAX_QUEUE = int(os.environ.get("EMBED_MAX_QUEUE", str(EMBED_MAX_CONCURRENCY * 4)))

# Bound to the running loop in startup() so excess concurrent inferences queue
# instead of stacking and blowing the memory ceiling (mirrors TTS _synth_semaphore).
_embed_semaphore: asyncio.Semaphore | None = None
# Admitted requests (running on the semaphore OR waiting for it). Mutated only from
# the single-threaded event loop, so the read-then-increment below is atomic without
# a lock. The load-shed gate reads this to reject before the queue grows unbounded.
_pending: int = 0

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


# Auth dependency list — applied to /embed and /v1/embeddings only in prod/staging.
# Dev mode serves both endpoints unauthenticated so tools without token-minting
# infrastructure (hooks, gitnexus, quick curl) can reach the service locally.
_AUTH_DEPS = [Depends(_require_embed_token)] if _IS_PROD else []


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
    global _embed_semaphore
    _embed_semaphore = asyncio.Semaphore(EMBED_MAX_CONCURRENCY)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _warmup)


@app.get("/mem")
def mem() -> Response:
    """Memory usage snapshot — unauthenticated for monitoring."""
    import resource
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports KB
    rss_mb = rss_kb / 1024 if os.uname().sysname == "Linux" else rss_kb / (1024 * 1024)
    return Response(
        content=f'{{"rss_mb":{rss_mb:.1f}}}',
        status_code=200,
        media_type="application/json",
    )


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


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Shared embed path for both endpoints — the single place the caps live.

    Order is deliberate (fail-closed on the cheap checks first): reject empty or
    oversized batches before touching readiness or the model, so a malformed request
    is rejected even while the model is still loading. The semaphore then bounds how
    many inferences run concurrently; excess requests queue rather than stacking.
    """
    if not texts:
        raise HTTPException(400, "inputs is required")
    if len(texts) > EMBED_MAX_BATCH:
        raise HTTPException(
            413, f"batch too large: {len(texts)} > EMBED_MAX_BATCH={EMBED_MAX_BATCH}"
        )
    if not _ready:
        raise HTTPException(503, "Embedding model loading")

    # Load-shed: refuse fast when the semaphore queue is already full, rather than
    # joining an unbounded wait. Atomic in the asyncio loop (no await between the
    # check and the increment). Retry-After tells well-behaved clients to back off.
    global _pending
    if _pending >= EMBED_MAX_CONCURRENCY + EMBED_MAX_QUEUE:
        raise HTTPException(
            503, "embedding service saturated, retry shortly",
            headers={"Retry-After": "1"},
        )
    _pending += 1

    from nomic_embed import _embed

    assert _embed_semaphore is not None, "semaphore not initialized (startup not run)"
    try:
        async with _embed_semaphore:
            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, lambda: _embed(texts))
                vectors = result.tolist()
                del result
                gc.collect()
                return vectors
            except Exception as exc:
                raise HTTPException(500, str(exc))
    finally:
        _pending -= 1


@app.post("/embed", dependencies=_AUTH_DEPS)
async def embed(req: EmbedRequest):
    """TEI-compatible batch embedding."""
    texts = [req.inputs] if isinstance(req.inputs, str) else req.inputs
    return await _embed_texts(texts)


@app.post("/v1/embeddings", dependencies=_AUTH_DEPS)
async def openai_embeddings(req: OpenAIEmbedRequest):
    """OpenAI-compatible embeddings endpoint for OpenWebUI RAG."""
    texts = [req.input] if isinstance(req.input, str) else req.input
    vectors = await _embed_texts(texts)

    return {
        "object": "list",
        "model": req.model,
        "data": [
            {"object": "embedding", "index": i, "embedding": vec}
            for i, vec in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }
