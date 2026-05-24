"""Normalize Claude Code and Codex hook payloads for Skill Radar.

The hook scripts should care about one small internal contract, not each
runtime's payload spelling. Unknown shapes return ``UnknownEvent`` so hooks
can keep the existing silent no-op discipline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


Runtime = Literal["claude", "codex", "unknown"]


@dataclass(frozen=True)
class PromptEvent:
    prompt: str
    runtime: Runtime
    hook_event_name: str = "UserPromptSubmit"


@dataclass(frozen=True)
class ToolEvent:
    tool_name: str
    command: str | None
    output: str
    exit_code: int | None
    runtime: Runtime
    hook_event_name: str = "PostToolUse"


@dataclass(frozen=True)
class UnknownEvent:
    reason: str
    runtime: Runtime = "unknown"


NormalizedEvent = PromptEvent | ToolEvent | UnknownEvent


PROMPT_FIELDS = (
    "prompt",
    "user_prompt",
    "message",
    "input",
    "text",
)

COMMAND_FIELDS = (
    "command",
    "cmd",
    "shell_command",
    "argv",
)

OUTPUT_FIELDS = (
    "tool_response",
    "tool_output",
    "output",
    "stdout",
    "stderr",
    "combined_output",
    "result",
    "response",
)

EXIT_CODE_FIELDS = (
    "exit_code",
    "exitCode",
    "status",
    "returncode",
    "code",
)


def normalize_event(payload: dict[str, Any]) -> NormalizedEvent:
    """Normalize a hook payload from Claude Code or Codex."""
    if not isinstance(payload, dict):
        return UnknownEvent("payload is not a JSON object")

    runtime = detect_runtime(payload)
    event_name = _event_name(payload)

    if event_name == "UserPromptSubmit" or _looks_like_prompt_payload(payload):
        prompt = _first_string(payload, PROMPT_FIELDS)
        if prompt:
            return PromptEvent(prompt=prompt.strip(), runtime=runtime)
        return UnknownEvent("prompt payload has no prompt text", runtime)

    if event_name == "PostToolUse" or _looks_like_tool_payload(payload):
        tool_name = _tool_name(payload)
        command = _command(payload)
        output = _output(payload)
        exit_code = _exit_code(payload)
        if tool_name or command or output:
            return ToolEvent(
                tool_name=tool_name or "",
                command=command,
                output=output,
                exit_code=exit_code,
                runtime=runtime,
            )
        return UnknownEvent("tool payload has no tool fields", runtime)

    return UnknownEvent("unrecognized hook payload", runtime)


def detect_runtime(payload: dict[str, Any]) -> Runtime:
    explicit = _first_string(payload, ("runtime", "agent_runtime", "source", "client"))
    if explicit:
        lowered = explicit.lower()
        if "codex" in lowered:
            return "codex"
        if "claude" in lowered:
            return "claude"

    if "tool_name" in payload or "tool_input" in payload or "tool_response" in payload:
        return "claude"

    # Codex payloads observed in app logs use snake/camel variants around
    # shell tool calls; keep this as a weak signal only.
    if (
        "hook_event_name" in payload
        or "hookEventName" in payload
        or "tool_call" in payload
        or "toolCall" in payload
    ):
        return "codex"

    return "unknown"


def _event_name(payload: dict[str, Any]) -> str | None:
    name = _first_string(payload, ("hook_event_name", "hookEventName", "event", "event_name", "type"))
    if not name:
        return None
    if name in ("UserPromptSubmit", "PostToolUse"):
        return name
    lowered = name.lower()
    if "prompt" in lowered and "submit" in lowered:
        return "UserPromptSubmit"
    if "tool" in lowered and ("post" in lowered or "use" in lowered):
        return "PostToolUse"
    return None


def _looks_like_prompt_payload(payload: dict[str, Any]) -> bool:
    if _first_string(payload, PROMPT_FIELDS):
        return not _looks_like_tool_payload(payload)
    return False


def _looks_like_tool_payload(payload: dict[str, Any]) -> bool:
    return bool(
        _tool_name(payload)
        or _command(payload)
        or _first_present(payload, OUTPUT_FIELDS) is not None
        or _nested_tool_payload(payload)
    )


def _tool_name(payload: dict[str, Any]) -> str | None:
    direct = _first_string(payload, ("tool_name", "toolName", "name"))
    if direct:
        return direct

    for key in ("tool", "tool_call", "toolCall", "call"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            value = _first_string(nested, ("name", "tool_name", "toolName", "recipient_name"))
            if value:
                return value

    # Codex desktop exposes shell execution as namespaced functions in some
    # logs; command presence is enough to treat it as a shell tool.
    if _command(payload):
        return "shell"
    return None


def _command(payload: dict[str, Any]) -> str | None:
    command = _first_string_or_argv(payload, COMMAND_FIELDS)
    if command:
        return command

    for key in ("tool_input", "toolInput", "input", "arguments", "args", "tool_call", "toolCall", "call"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            command = _first_string_or_argv(nested, COMMAND_FIELDS)
            if command:
                return command

    return None


def _output(payload: dict[str, Any]) -> str:
    pieces: list[str] = []
    for value in _iter_values(payload, OUTPUT_FIELDS):
        text = _stringify_output(value)
        if text:
            pieces.append(text)

    for key in ("tool_response", "toolResponse", "response", "result", "output"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            for value in _iter_values(nested, OUTPUT_FIELDS):
                text = _stringify_output(value)
                if text:
                    pieces.append(text)

    return "\n".join(dict.fromkeys(pieces))


def _exit_code(payload: dict[str, Any]) -> int | None:
    direct = _first_int(payload, EXIT_CODE_FIELDS)
    if direct is not None:
        return direct

    for key in ("tool_response", "toolResponse", "response", "result", "output"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            value = _first_int(nested, EXIT_CODE_FIELDS)
            if value is not None:
                return value
    return None


def _nested_tool_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("tool", "tool_call", "toolCall", "call", "tool_input", "toolInput"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    return None


def _first_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    value = _first_present(payload, keys)
    return value if isinstance(value, str) and value.strip() else None


def _first_string_or_argv(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    value = _first_present(payload, keys)
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return " ".join(value)
    return None


def _first_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    value = _first_present(payload, keys)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _iter_values(payload: dict[str, Any], keys: tuple[str, ...]):
    for key in keys:
        if key in payload:
            yield payload[key]


def _stringify_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        pieces = []
        for key in ("stdout", "stderr", "output", "message", "text", "error"):
            item = value.get(key)
            if isinstance(item, str) and item:
                pieces.append(item)
        return "\n".join(pieces)
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)
