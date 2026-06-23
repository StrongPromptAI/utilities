"""Runtime query embedding via the shared-svcs embed service (nomic 768d).

Same model + vector space that build_brain ingested into. The service embeds each
tool-query through this HTTP path; tests inject a local ONNX embedder instead (see
app.state.embed_fn). Fail-fast on missing config.
"""
from __future__ import annotations

import os
import time

import httpx
import jwt

EMBED_URL = os.environ.get("EMBED_URL", "")                       # e.g. https://shared-svcs-embed.up.railway.app/embed
SHARED_SVC_JWT_SECRET = os.environ.get("SHARED_SVC_JWT_SECRET", "")  # == embed service JWT_SECRET
SERVICE_NAME = os.environ.get("SERVICE_NAME", "coach")


def _embed_token(ttl_seconds: int = 1800) -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": SERVICE_NAME, "aud": "embed", "iat": now, "exp": now + ttl_seconds},
        SHARED_SVC_JWT_SECRET,
        algorithm="HS256",
    )


def embed_query(text: str) -> list[float]:
    if not EMBED_URL or not SHARED_SVC_JWT_SECRET:
        raise RuntimeError("FAIL-FAST: EMBED_URL + SHARED_SVC_JWT_SECRET required for runtime embedding")
    r = httpx.post(
        EMBED_URL,
        json={"inputs": [text]},
        headers={"Authorization": f"Bearer {_embed_token()}"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()[0]
