import json
import urllib.request
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "radar"))

from event_adapter import PromptEvent, ToolEvent, UnknownEvent, normalize_event
from output_adapter import render_additional_context
import embed_client


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_event_adapter_parses_claude_prompt():
    event = normalize_event(load_fixture("claude_user_prompt_submit.json"))

    assert isinstance(event, PromptEvent)
    assert event.runtime == "claude"
    assert "skill radar" in event.prompt


def test_event_adapter_parses_codex_prompt():
    event = normalize_event(load_fixture("codex_user_prompt_submit.json"))

    assert isinstance(event, PromptEvent)
    assert event.runtime == "codex"
    assert "skill radar" in event.prompt


def test_event_adapter_parses_claude_tool_error():
    event = normalize_event(load_fixture("claude_post_tool_use_error.json"))

    assert isinstance(event, ToolEvent)
    assert event.runtime == "claude"
    assert event.tool_name == "Bash"
    assert event.command == "uv run pytest tests/test_missing.py -q"
    assert "not found" in event.output


def test_event_adapter_parses_codex_tool_error():
    event = normalize_event(load_fixture("codex_post_tool_use_error.json"))

    assert isinstance(event, ToolEvent)
    assert event.runtime == "codex"
    assert event.tool_name == "functions.exec_command"
    assert event.command == "uv run pytest tests/test_missing.py -q"
    assert event.exit_code == 4
    assert "not found" in event.output


def test_event_adapter_parses_codex_nested_tool_command():
    event = normalize_event(load_fixture("codex_post_tool_use_grep.json"))

    assert isinstance(event, ToolEvent)
    assert event.runtime == "codex"
    assert event.command == "rg normalize_event scripts/radar"
    assert "event_adapter.py" in event.output


def test_event_adapter_unknown_payload_noops():
    event = normalize_event({"hello": "world"})

    assert isinstance(event, UnknownEvent)


def test_output_adapter_preserves_additional_context_envelope():
    rendered = render_additional_context(
        "Skill Radar context",
        hook_event_name="UserPromptSubmit",
        runtime="codex",
    )

    payload = json.loads(rendered)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert payload["hookSpecificOutput"]["additionalContext"] == "Skill Radar context"


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return b"[[0.1,0.2,0.3]]"


def test_embed_client_uses_local_onnx_service_without_auth(monkeypatch):
    captured = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _FakeResponse()

    monkeypatch.setattr(embed_client, "EMBED_URL", "http://localhost:8100/embed")
    monkeypatch.setattr(embed_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        embed_client,
        "_make_token",
        lambda: (_ for _ in ()).throw(AssertionError("local embed must not mint JWT")),
    )

    vectors = embed_client.embed(["skill radar"], timeout=1.5, retries=0)

    assert vectors == [[0.1, 0.2, 0.3]]
    assert captured["url"] == "http://localhost:8100/embed"
    assert captured["timeout"] == 1.5
    assert captured["headers"].get("Content-type") == "application/json"
    assert "Authorization" not in captured["headers"]
    assert json.loads(captured["body"]) == {"inputs": ["skill radar"]}


def test_embed_client_adds_bearer_token_for_remote_override(monkeypatch):
    captured = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        return _FakeResponse()

    monkeypatch.setattr(embed_client, "EMBED_URL", "https://shared-svcs-embed.up.railway.app/embed")
    monkeypatch.setattr(embed_client, "_make_token", lambda: "signed-token")
    monkeypatch.setattr(embed_client.urllib.request, "urlopen", fake_urlopen)

    vectors = embed_client.embed(["remote fallback"], retries=0)

    assert vectors == [[0.1, 0.2, 0.3]]
    assert captured["url"] == "https://shared-svcs-embed.up.railway.app/embed"
    assert captured["headers"]["Authorization"] == "Bearer signed-token"
