"""Embed-client failure-classification + verify-self-test contract.

Guards the loud-on-auth behavior (plan 26-6-18): a wrong/expired JWT (401/403) must
fail LOUD via EmbedAuthError rather than silently degrade to None and leave the radar
dark. This is the only standing gate on that path — there is no human code review on
this repo, so an untested classification branch is a control that doesn't exist.
"""

import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "radar"))

import embed_client as ec  # noqa: E402


def _http(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://remote.example/embed", code, str(code), {}, io.BytesIO(b"")
    )


@pytest.fixture
def remote(monkeypatch):
    """Point embed_client at a remote endpoint without reading keys.json."""
    monkeypatch.setattr(ec, "EMBED_URL", "https://remote.example/embed")
    monkeypatch.setattr(ec, "_make_token", lambda: "tok")


def _patch_urlopen(monkeypatch, exc):
    def _raise(req, timeout=None):
        raise exc

    monkeypatch.setattr(ec.urllib.request, "urlopen", _raise)


# --- exception hierarchy: the load-bearing invariant the hooks rely on ---


def test_auth_error_is_unavailable_subclass():
    # Both hooks catch `except EmbedUnavailable -> sys.exit(2)`. EmbedAuthError MUST
    # subclass it so a bad JWT inherits the loud exit instead of degrading to None.
    assert issubclass(ec.EmbedAuthError, ec.EmbedUnavailable)


# --- HTTP status classification: down / auth / busy ---


@pytest.mark.parametrize("code", [401, 403])
def test_auth_codes_raise_auth_error(monkeypatch, remote, code):
    _patch_urlopen(monkeypatch, _http(code))
    with pytest.raises(ec.EmbedAuthError):
        ec.embed(["x"], timeout=0.01, retries=0)


@pytest.mark.parametrize("code", [502, 504, 408])
def test_gateway_codes_raise_unavailable(monkeypatch, remote, code):
    _patch_urlopen(monkeypatch, _http(code))
    with pytest.raises(ec.EmbedUnavailable) as ei:
        ec.embed(["x"], timeout=0.01, retries=0)
    assert not isinstance(ei.value, ec.EmbedAuthError)  # down, not auth


def test_503_degrades_as_plain_httperror(monkeypatch, remote):
    # 503 = up-but-shedding: NOT EmbedUnavailable, so the hook wrapper's
    # `except Exception: return None` degrades it for that turn (transient).
    _patch_urlopen(monkeypatch, _http(503))
    with pytest.raises(urllib.error.HTTPError) as ei:
        ec.embed(["x"], timeout=0.01, retries=0)
    assert not isinstance(ei.value, ec.EmbedUnavailable)


def test_connection_refused_raises_unavailable(monkeypatch, remote):
    _patch_urlopen(monkeypatch, ConnectionRefusedError("refused"))
    with pytest.raises(ec.EmbedUnavailable):
        ec.embed(["x"], timeout=0.01, retries=0)


# --- _selftest exit codes: 0 OK / 2 unreachable / 3 auth-rejected ---


def test_selftest_ok(monkeypatch):
    monkeypatch.setattr(ec, "EMBED_URL", "http://localhost:8100/embed")  # local: skip batch poke
    monkeypatch.setattr(ec, "embed", lambda *a, **k: [[0.0] * 768])
    assert ec._selftest() == 0


def test_selftest_auth_rejected(monkeypatch):
    def _raise(*a, **k):
        raise ec.EmbedAuthError("auth")

    monkeypatch.setattr(ec, "embed", _raise)
    assert ec._selftest() == 3


def test_selftest_unreachable(monkeypatch):
    def _raise(*a, **k):
        raise ec.EmbedUnavailable("down")

    monkeypatch.setattr(ec, "embed", _raise)
    assert ec._selftest() == 2


# --- structural: both hooks wire loud-on-auth (GLM review: assert the hook exit) ---


@pytest.mark.parametrize("hook", ["radar_prompt.py", "radar_post_tool.py"])
def test_hooks_fail_loud_on_auth(hook):
    src = (ROOT / "scripts" / "radar" / hook).read_text()
    assert "EmbedAuthError" in src
    assert "isinstance(e, EmbedAuthError)" in src
    assert "except EmbedUnavailable" in src
    assert "sys.exit(2)" in src
