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

Failure taxonomy (what callers can rely on):
    • No backend reachable at all (connection refused / DNS / timeout, or a
      502/504 gateway-dead after retries) → raises EmbedUnavailable. The two
      Claude Code hooks catch this and fail LOUD (exit 2) rather than silently
      losing radar coverage.
    • Auth rejected (401/403 — missing / wrong / expired JWT) → raises
      EmbedAuthError, a subclass of EmbedUnavailable so it is loud too. A bad
      secret is deterministic (wrong every turn), so it must announce itself,
      not degrade — it is the one misconfig that would otherwise hide.
    • Up but shedding load (503) → raises a plain HTTPError; the hook wrappers
      degrade it to None for that turn (genuinely transient, not an outage).

Setup check: `python scripts/radar/embed_client.py verify` round-trips the
configured endpoint (and, when remote, the JWT) before you trust the radar.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

class EmbedUnavailable(RuntimeError):
    """No embed backend could be reached (connection refused / DNS / timeout, or a
    502/504 gateway after retries) — i.e. neither local ONNX nor the Railway box is
    available. Distinct from a 503 (the service IS up, just shedding load): callers
    hard-fail loudly on this, but may degrade on a transient busy signal."""


class EmbedAuthError(EmbedUnavailable):
    """The endpoint responded but rejected the JWT (401/403) — a missing, wrong, or
    expired shared_svc_jwt_secret. Subclasses EmbedUnavailable so callers that already
    fail loud on "down" fail loud here too: a bad secret is deterministic (wrong every
    turn), not a transient busy signal, so it must announce itself rather than degrade
    to None and leave the radar silently dark."""


EMBED_URL = os.environ.get("SKILL_RADAR_EMBED_URL", "http://localhost:8100/embed")
# Bulk endpoint, used ONLY by the index-build scripts (batch=True) and ONLY when
# EMBED_URL is remote. Two use cases, one rule:
#   • local ONNX available → EMBED_URL is localhost → serve EVERYTHING from it
#     (reindex + hook); it's free, single-box, no reason to split.
#   • forced to Railway → EMBED_URL is remote → split: heavy reindex goes here
#     (embed-batch: 8 GB, hibernating, sized for bulk), the hook stays on the small
#     always-on interactive box so a rebuild can't OOM it.
# embed-batch shares the aud="embed" JWT secret, so the same token works for both.
BATCH_EMBED_URL = os.environ.get(
    "SKILL_RADAR_BATCH_EMBED_URL", "https://embed-batch-production.up.railway.app/embed"
)
SERVICE_NAME = "skill-radar"  # iss claim on the JWT
TOKEN_TTL_SECONDS = 1800  # 30 min

_KEYS_PATH = Path.home() / ".config/keys.json"
_LOCAL_HOSTS = ("://localhost", "://127.0.0.1", "://[::1]")


def _is_local_endpoint(url: str) -> bool:
    return any(host in url for host in _LOCAL_HOSTS)


def _resolve_url(batch: bool) -> str:
    """Endpoint for this call. Bulk work (batch=True) is routed to BATCH_EMBED_URL
    ONLY when the configured endpoint is remote; when EMBED_URL is local, local ONNX
    serves everything (no split). The hook (batch=False) always uses EMBED_URL."""
    if batch and not _is_local_endpoint(EMBED_URL):
        return BATCH_EMBED_URL
    return EMBED_URL


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


def embed(texts: list[str], *, batch: bool = False, timeout: float = 10.0, retries: int = 3) -> list[list[float]]:
    """POST texts to the embed service, return embedding vectors.

    Set batch=True for bulk index-build work: when the endpoint is remote this
    routes to the dedicated embed-batch box (see BATCH_EMBED_URL); when local it
    stays on local ONNX. The hook leaves batch=False (interactive endpoint).

    Retries on HTTP 502/503/504 with exponential backoff (remote endpoints'
    edges occasionally 502 during cold-start or worker handover). After
    `retries` exhausted or on other error, propagates the exception. Local
    endpoints also retry — transient socket errors during local service
    restart are handled the same way.
    """
    url = _resolve_url(batch)
    payload = json.dumps({"inputs": texts}).encode()
    is_local = _is_local_endpoint(url)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        headers = {"Content-Type": "application/json"}
        if not is_local:
            headers["Authorization"] = f"Bearer {_make_token()}"
        req = urllib.request.Request(url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (502, 503, 504) and attempt < retries:
                time.sleep(0.5 * (2 ** attempt))  # 0.5s, 1s, 2s
                continue
            # 502/504/408 after retries = gateway dead / box crashed (our OOM returned
            # 502) / request never completed → treat as unavailable (down). 503 = up
            # but shedding (transient, degrade); 4xx = responded → raise as-is so
            # callers can tell "busy/misconfigured" from "down".
            if e.code in (502, 504, 408):
                raise EmbedUnavailable(f"embed gateway unreachable ({e.code}) at {url}") from e
            if e.code in (401, 403):
                raise EmbedAuthError(
                    f"embed auth rejected ({e.code}) at {url} — check shared_svc_jwt_secret"
                ) from e
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.5 * (2 ** attempt))
                continue
            # Connection refused / DNS / socket timeout — no HTTP response at all.
            raise EmbedUnavailable(f"embed unreachable at {url}: {e}") from e
    # Unreachable — loop either returns or raises
    raise EmbedUnavailable(f"embed failed after {retries} retries: {last_err}")


def _health_url(url: str) -> str:
    base = url[:-len("/embed")] if url.endswith("/embed") else url.rsplit("/", 1)[0]
    return base + "/health"


def wait_for_ready(*, batch: bool = False, timeout: float = 90.0, interval: float = 3.0) -> None:
    """Poll the resolved endpoint's /health until 200, then return.

    Wakes a hibernating embed-batch (cold start ~30-40s) before a reindex floods it.
    embed()'s short per-request retry budget can't cover a cold start, so build
    scripts that target the batch box call this first. Fast no-op when already warm;
    local endpoints answer immediately. /health is public (no token). Raises
    RuntimeError if not ready by `timeout` — fail loud, don't reindex into a dead box."""
    health = _health_url(_resolve_url(batch))
    deadline = time.time() + timeout
    while True:
        try:
            with urllib.request.urlopen(health, timeout=10) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        if time.time() >= deadline:
            raise RuntimeError(f"embed endpoint not ready after {timeout:g}s: {health}")
        time.sleep(interval)


def _selftest() -> int:
    """One-shot setup verification — run on a new box before trusting the radar:

        uv run --project ~/repos/utilities python scripts/radar/embed_client.py verify

    Confirms the configured interactive endpoint answers, and — when remote — that the
    JWT is accepted (the one failure that otherwise degrades silently). Uses the SAME
    embed() the hooks use, so it can't drift from real behavior.

    Exit codes: 0 OK · 2 unreachable (box down / wrong URL) · 3 auth rejected (bad JWT)."""
    url = _resolve_url(batch=False)
    mode = "local (no auth)" if _is_local_endpoint(url) else "remote (JWT-signed)"
    print(f"Skill Radar embed self-test\n  interactive endpoint: {url}  [{mode}]")
    try:
        vec = embed(["radar embed self-test"], timeout=10.0, retries=2)[0]
    except EmbedAuthError as e:
        print(
            f"  ❌ AUTH REJECTED — {e}\n"
            "     Fix shared_svc_jwt_secret in ~/.config/keys.json (= the shared-svcs "
            "Railway JWT_SECRET), or unset SKILL_RADAR_EMBED_URL to use local ONNX.",
            file=sys.stderr,
        )
        return 3
    except EmbedUnavailable as e:
        print(
            f"  ❌ UNREACHABLE — {e}\n"
            "     Start the endpoint (local ONNX on :8100) or fix SKILL_RADAR_EMBED_URL.",
            file=sys.stderr,
        )
        return 2
    except Exception as e:  # noqa: BLE001 — self-test reports any failure, never raises
        print(f"  ❌ FAILED — {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    print(f"  ✅ OK — got a {len(vec)}-dim vector.")

    # Best-effort batch-box poke (remote only). A hibernating embed-batch is NORMAL —
    # warn, don't fail; reindex wakes it via wait_for_ready().
    if not _is_local_endpoint(EMBED_URL):
        batch_url = _resolve_url(batch=True)
        print(f"  batch endpoint:       {batch_url}")
        try:
            wait_for_ready(batch=True, timeout=5.0, interval=2.0)
            print("  ✅ batch box awake.")
        except Exception:
            print(
                "  ⚠️  batch box not ready in 5s — likely hibernating (normal); "
                "reindex wakes it via wait_for_ready()."
            )
    return 0


if __name__ == "__main__":
    _cmd = sys.argv[1] if len(sys.argv) > 1 else "verify"
    if _cmd in ("verify", "selftest", "--verify", "--selftest"):
        sys.exit(_selftest())
    print(f"unknown command: {_cmd}\nusage: embed_client.py [verify]", file=sys.stderr)
    sys.exit(64)
