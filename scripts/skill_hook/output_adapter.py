"""Render Skill Radar hook output for supported runtimes."""

from __future__ import annotations

import json
from typing import Literal


Runtime = Literal["claude", "codex", "unknown"]


def render_additional_context(
    additional_context: str,
    *,
    hook_event_name: str,
    runtime: Runtime = "unknown",
) -> str:
    """Return the JSON envelope consumed by Claude Code and Codex hooks.

    Codex's local hook config mirrors Claude's event names and accepts the same
    additional-context envelope in current builds. Keeping one renderer avoids
    drift while leaving a single edit point if Codex's contract diverges.
    """
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": hook_event_name,
                "additionalContext": additional_context,
            }
        }
    )
