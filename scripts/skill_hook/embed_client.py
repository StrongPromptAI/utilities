"""
Embed client for Skill Radar — calls the shared-svcs embed service.

Reads the JWT secret from ~/.config/keys.json (key: `shared_svc_jwt_secret`),
which is populated by pulling `JWT_SECRET` from the shared-svcs Railway
project. Single round-trip per call; 30-min JWT regenerated each call (cheap).

Replaces the prior localhost:8100 embed service. The local ONNX service is
deprecated — this client reaches shared-svcs over HTTPS with zero local
infrastructure required.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt

EMBED_URL = os.environ.get("SKILL_RADAR_EMBED_URL", "https://shared-svcs-embed.up.railway.app/embed")
SERVICE_NAME = "skill-radar"  # iss claim on the JWT
TOKEN_TTL_SECONDS = 1800  # 30 min

_KEYS_PATH = Path.home() / ".config/keys.json"


def _load_secret() -> str:
    """Load SHARED_SVC_JWT_SECRET from ~/.config/keys.json.

    Env var takes precedence (for tests); keys.json is the normal path.
    """
    env = os.environ.get("SHARED_SVC_JWT_SECRET")
    if env:
        return env
    try:
        data = json.loads(_KEYS_PATH.read_text())
    except Exception as e:
        raise RuntimeError(
            f"Could not read {_KEYS_PATH}: {e}. "
            "Run the Railway pull to populate shared_svc_jwt_secret."
        )
    secret = data.get("shared_svc_jwt_secret")
    if not secret:
        raise RuntimeError(
            f"{_KEYS_PATH} has no 'shared_svc_jwt_secret' key. "
            "Pull it from Railway shared-svcs project and store there."
        )
    return secret


def _make_token() -> str:
    return jwt.encode(
        {
            "iss": SERVICE_NAME,
            "aud": "embed",
            "exp": int(time.time()) + TOKEN_TTL_SECONDS,
        },
        _load_secret(),
        algorithm="HS256",
    )


def embed(texts: list[str], *, timeout: float = 10.0, retries: int = 3) -> list[list[float]]:
    """POST texts to shared-svcs embed, return embedding vectors.

    Retries on HTTP 502/503/504 with exponential backoff (Railway's edge
    occasionally 502s during cold-start or worker handover). After `retries`
    exhausted or on other error, propagates the exception.
    """
    payload = json.dumps({"inputs": texts}).encode()
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            EMBED_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_make_token()}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (502, 503, 504) and attempt < retries:
                time.sleep(0.5 * (2 ** attempt))  # 0.5s, 1s, 2s
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            raise
    # Unreachable — loop either returns or raises
    raise RuntimeError(f"embed failed after {retries} retries: {last_err}")
