"""
Embed client for Skill Radar.

Default endpoint: http://localhost:8100/embed — the local (free, fast) embed
service. Local is the policy default for every caller; override only by
explicit env var.

Override:
    SKILL_RADAR_EMBED_URL=<url>    e.g., to point at the shared-svcs Railway
                                   endpoint for a machine that doesn't host
                                   the local service.

Authentication:
    Local URLs (localhost, 127.0.0.1, ::1) skip authentication — the local
    embed service has no auth layer.
    Remote URLs sign each call with a JWT from ~/.config/keys.json key
    `shared_svc_jwt_secret` (pulled from the shared-svcs Railway project's
    JWT_SECRET). JWT secret is only loaded lazily when a remote URL is used,
    so a machine with no keys.json entry still works for local-only flows.

Callers that cannot tolerate the local service being down (e.g., the two
Claude Code hooks that must not block the editor on a 5s timeout) should
either set SKILL_RADAR_EMBED_URL to a remote endpoint, or accept that their
embed calls will silently fail when local is unreachable. Silent failure is
preferable to an unexpected remote network hop the user didn't ask for.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

EMBED_URL = os.environ.get("SKILL_RADAR_EMBED_URL", "http://localhost:8100/embed")
SERVICE_NAME = "skill-radar"  # iss claim on the JWT
TOKEN_TTL_SECONDS = 1800  # 30 min

_KEYS_PATH = Path.home() / ".config/keys.json"
_LOCAL_HOSTS = ("://localhost", "://127.0.0.1", "://[::1]")


def _is_local_endpoint(url: str) -> bool:
    return any(host in url for host in _LOCAL_HOSTS)


def _load_secret() -> str:
    """Load SHARED_SVC_JWT_SECRET from ~/.config/keys.json.

    Env var takes precedence (for tests); keys.json is the normal path.
    Only called when EMBED_URL is remote — local flows never touch this.
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
    # Import lazily so machines that only use local embed don't need PyJWT.
    import jwt
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
    """POST texts to the embed service, return embedding vectors.

    Retries on HTTP 502/503/504 with exponential backoff (remote endpoints'
    edges occasionally 502 during cold-start or worker handover). After
    `retries` exhausted or on other error, propagates the exception. Local
    endpoints also retry — transient socket errors during local service
    restart are handled the same way.
    """
    payload = json.dumps({"inputs": texts}).encode()
    is_local = _is_local_endpoint(EMBED_URL)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        headers = {"Content-Type": "application/json"}
        if not is_local:
            headers["Authorization"] = f"Bearer {_make_token()}"
        req = urllib.request.Request(EMBED_URL, data=payload, headers=headers)
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
