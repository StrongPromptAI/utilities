"""
Shared-svcs JWT token generator for calling projects.

Copy this file into each project that calls the shared STT or embed service.
Required env vars:
  SHARED_SVC_JWT_SECRET — same value as JWT_SECRET on the shared-svcs Railway project
  SERVICE_NAME          — identifies this caller in the iss claim (e.g. "itheraputix")

Usage:
  from shared_auth.token import make_stt_token, make_embed_token

  # Backend endpoint to issue browser STT tokens:
  @router.get("/api/stt-token")
  async def stt_token(user=Depends(require_auth)):
      return {"token": make_stt_token()}

  # Backend embed call:
  token = make_embed_token()
  httpx.post(EMBED_URL, json={"inputs": texts}, headers={"Authorization": f"Bearer {token}"})
"""

import os
import time

import jwt

_SECRET = os.environ["SHARED_SVC_JWT_SECRET"]
_SERVICE = os.environ["SERVICE_NAME"]


def make_stt_token(ttl_seconds: int = 300) -> str:
    """5-min JWT for one browser STT connection (aud=stt). Refetch on every reconnect."""
    return jwt.encode(
        {"iss": _SERVICE, "aud": "stt", "exp": int(time.time()) + ttl_seconds},
        _SECRET,
        algorithm="HS256",
    )


def make_embed_token(ttl_seconds: int = 1800) -> str:
    """30-min JWT for backend embed calls (aud=embed). Regenerate before expiry."""
    return jwt.encode(
        {"iss": _SERVICE, "aud": "embed", "exp": int(time.time()) + ttl_seconds},
        _SECRET,
        algorithm="HS256",
    )
