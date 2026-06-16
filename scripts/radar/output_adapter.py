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


def _attr(value: str) -> str:
    """Escape a string for use as a double-quoted XML attribute value, so the
    `<radar …>` opening tag is always well-formed. A no-op for the clean
    `<category>:<pointer>` sources the radar produces (component_key, schema
    path, rule title), but defensive against a doctrine title carrying `&`/`"`.
    """
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_radar_block(body: str, *, source: str, trust: str) -> str:
    """Wrap one corpus's matched content in the shared provenance block
    (plan thj/26-6-16 Phase 1). Every radar corpus — skill, schema, doctrine,
    protocol — renders through this single shape so a stacked injection is
    legible: each block declares WHERE it came from and HOW MUCH to trust it.

    - ``source`` — ``<category>:<pointer>``; the prefix carries the corpus
      (``skill:`` / ``schema:`` / ``doctrine:`` / ``protocol:``). The pointer
      MUST be agent-legible (a grep-able key / path / component_key, never a
      UUID) so ``trust=…:verify`` is actionable.
    - ``trust`` — the verify-posture, the one field that changes behavior
      (``live-oracle`` | ``cached:verify-vs-live`` |
      ``committed:may-lag-uncommitted`` | ``learned:judge-applicability``).

    NO ``score`` attribute by design: match confidence stays retrieval-side so
    an LLM can't cross-rank corpora whose scores aren't comparable. The body is
    emitted verbatim (it's LLM display context, not re-parsed XML); only the
    attribute values are escaped, keeping the opening tag well-formed.
    """
    return f'<radar source="{_attr(source)}" trust="{_attr(trust)}">\n{body}\n</radar>'
