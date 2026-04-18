"""
Health and auth integration tests for shared-svcs (STT + embed).

Run after deploying to Railway:
  uv run pytest services/tests/test_shared_svcs.py -v

Required env vars:
  STT_URL              = wss://stt.shared-svcs.up.railway.app/transcribe
  EMBED_URL            = https://embed.shared-svcs.up.railway.app
  SHARED_SVC_JWT_SECRET
  SERVICE_NAME         = test-runner
"""

import asyncio
import os
import time

import httpx
import jwt
import pytest
import websockets

STT_WS_URL  = os.environ["STT_URL"]   # wss://...
EMBED_URL   = os.environ["EMBED_URL"] # https://...
SECRET      = os.environ["SHARED_SVC_JWT_SECRET"]
SERVICE     = os.environ.get("SERVICE_NAME", "test-runner")

# Derive STT HTTP base from WS URL for /health calls
_STT_HTTP = STT_WS_URL.replace("wss://", "https://").replace("ws://", "http://").removesuffix("/transcribe")


def _tok(aud: str, ttl: int = 300) -> str:
    return jwt.encode({"iss": SERVICE, "aud": aud, "exp": int(time.time()) + ttl}, SECRET, algorithm="HS256")


# ---------------------------------------------------------------------------
# Health — poll until ready (model load can take ~30s after cold deploy)
# ---------------------------------------------------------------------------

def test_stt_health_ready():
    """Poll STT /health until 200 — validates model fully loaded before auth tests."""
    for _ in range(30):
        r = httpx.get(f"{_STT_HTTP}/health", timeout=10)
        if r.status_code == 200:
            data = r.json()
            assert data["status"] == "ok"
            assert data["model"] == "stt"
            return
        assert r.status_code == 503, f"Unexpected status {r.status_code}: {r.text}"
        time.sleep(10)
    pytest.fail("STT /health never returned 200 within 300s")


def test_embed_health_ready():
    """Poll embed /health until 200."""
    for _ in range(18):
        r = httpx.get(f"{EMBED_URL}/health", timeout=10)
        if r.status_code == 200:
            data = r.json()
            assert data["status"] == "ok"
            assert data["dims"] == 768
            return
        assert r.status_code == 503, f"Unexpected status {r.status_code}: {r.text}"
        time.sleep(10)
    pytest.fail("Embed /health never returned 200 within 180s")


def test_health_no_sensitive_leakage():
    """Health endpoints must not expose env vars, build info, or stack traces."""
    for url in [f"{_STT_HTTP}/health", f"{EMBED_URL}/health"]:
        r = httpx.get(url, timeout=10)
        body = r.text.lower()
        for bad in ["secret", "password", "token", "traceback", "exception", "environ"]:
            assert bad not in body, f"{url} leaks '{bad}' in health response"


# ---------------------------------------------------------------------------
# Embed — Bearer auth
# ---------------------------------------------------------------------------

def test_embed_valid_token():
    token = _tok("embed")
    r = httpx.post(f"{EMBED_URL}/embed", json={"inputs": ["hello world"]},
                   headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert r.status_code == 200
    vecs = r.json()
    assert len(vecs) == 1
    assert len(vecs[0]) == 768


def test_embed_no_token_rejected():
    r = httpx.post(f"{EMBED_URL}/embed", json={"inputs": ["hello"]}, timeout=10)
    assert r.status_code in (401, 403)


def test_embed_invalid_token_rejected():
    r = httpx.post(f"{EMBED_URL}/embed", json={"inputs": ["hello"]},
                   headers={"Authorization": "Bearer garbage.token.value"}, timeout=10)
    assert r.status_code == 401


def test_embed_wrong_audience_rejected():
    """STT-scoped token must not authenticate against embed."""
    token = _tok("stt")
    r = httpx.post(f"{EMBED_URL}/embed", json={"inputs": ["hello"]},
                   headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert r.status_code == 401


def test_embed_expired_token_rejected():
    token = _tok("embed", ttl=-10)
    r = httpx.post(f"{EMBED_URL}/embed", json={"inputs": ["hello"]},
                   headers={"Authorization": f"Bearer {token}"}, timeout=10)
    assert r.status_code == 401


def test_embed_openai_compat_endpoint():
    """v1/embeddings also requires auth."""
    token = _tok("embed")
    r = httpx.post(f"{EMBED_URL}/v1/embeddings",
                   json={"input": ["hello world"], "model": "nomic-embed-text-v1.5"},
                   headers={"Authorization": f"Bearer {token}"}, timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert len(data["data"][0]["embedding"]) == 768


def test_embed_openai_compat_no_token():
    r = httpx.post(f"{EMBED_URL}/v1/embeddings",
                   json={"input": ["hello"]}, timeout=10)
    assert r.status_code in (401, 403)


# ---------------------------------------------------------------------------
# STT WebSocket — first-frame auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stt_no_first_frame_closes_4401():
    """Connect but send nothing for 6s — server must close with 4401."""
    async with websockets.connect(STT_WS_URL) as ws:
        try:
            await asyncio.wait_for(ws.recv(), timeout=6.0)
            pytest.fail("Expected connection to close, got a message instead")
        except websockets.ConnectionClosed as e:
            assert e.code == 4401, f"Expected 4401, got {e.code}"
        except asyncio.TimeoutError:
            pytest.fail("Server did not close idle connection within 6s")


@pytest.mark.asyncio
async def test_stt_invalid_token_closes_4401():
    async with websockets.connect(STT_WS_URL) as ws:
        await ws.send("not.a.valid.jwt")
        try:
            await asyncio.wait_for(ws.recv(), timeout=3.0)
            pytest.fail("Expected connection to close")
        except websockets.ConnectionClosed as e:
            assert e.code == 4401


@pytest.mark.asyncio
async def test_stt_wrong_audience_closes_4401():
    """Embed-scoped token must be rejected by STT."""
    token = _tok("embed")
    async with websockets.connect(STT_WS_URL) as ws:
        await ws.send(token)
        try:
            await asyncio.wait_for(ws.recv(), timeout=3.0)
            pytest.fail("Expected connection to close")
        except websockets.ConnectionClosed as e:
            assert e.code == 4401


@pytest.mark.asyncio
async def test_stt_expired_token_closes_4401():
    token = _tok("stt", ttl=-10)
    async with websockets.connect(STT_WS_URL) as ws:
        await ws.send(token)
        try:
            await asyncio.wait_for(ws.recv(), timeout=3.0)
            pytest.fail("Expected connection to close")
        except websockets.ConnectionClosed as e:
            assert e.code == 4401


@pytest.mark.asyncio
async def test_stt_valid_token_stays_open():
    """Valid token — connection must survive a ping/pong exchange."""
    token = _tok("stt")
    async with websockets.connect(STT_WS_URL) as ws:
        await ws.send(token)   # first frame = JWT
        await ws.send("ping")  # keepalive
        # 2s silence = auth passed, no immediate close
        try:
            await asyncio.wait_for(ws.recv(), timeout=2.0)
        except asyncio.TimeoutError:
            pass  # silence is correct — no transcript without audio


# ---------------------------------------------------------------------------
# STT — token expiry forces reconnect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stt_server_closes_at_token_expiry():
    """10s token — server closes with 1001 near expiry; client must reconnect."""
    token = _tok("stt", ttl=10)
    async with websockets.connect(STT_WS_URL) as ws:
        await ws.send(token)
        start = time.time()
        while True:
            try:
                await ws.send("ping")
                await asyncio.sleep(2)
            except websockets.ConnectionClosed as e:
                assert e.code == 1001, f"Expected 1001 (token expired), got {e.code}"
                elapsed = time.time() - start
                assert 8 <= elapsed <= 15, f"Server closed at {elapsed:.1f}s, expected 8–15s window"
                break
            if time.time() - start > 20:
                pytest.fail("Server did not close connection within 20s of token expiry")

    # Verify reconnect with fresh token works
    fresh_token = _tok("stt")
    async with websockets.connect(STT_WS_URL) as ws2:
        await ws2.send(fresh_token)
        await ws2.send("ping")
        try:
            await asyncio.wait_for(ws2.recv(), timeout=2.0)
        except asyncio.TimeoutError:
            pass  # silence = reconnect succeeded


# ---------------------------------------------------------------------------
# STT — concurrent connections (memory/vCPU headroom)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stt_three_concurrent_connections():
    """Three simultaneous authenticated connections — all must stay open for 3s."""
    async def connect_and_hold(idx: int) -> bool:
        token = _tok("stt")
        try:
            async with websockets.connect(STT_WS_URL) as ws:
                await ws.send(token)
                await ws.send("ping")
                await asyncio.sleep(3)
            return True
        except Exception as e:
            pytest.fail(f"Connection {idx} failed: {e}")
            return False

    results = await asyncio.gather(*[connect_and_hold(i) for i in range(3)])
    assert all(results)
