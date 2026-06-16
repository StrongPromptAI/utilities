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


# Trust-value classification (plan thj/26-6-16 Phase 2a). Every ``trust`` value
# is one of two kinds:
#   - VERIFY-INVITATION — true by construction, tells the agent to go check
#     (``cached:verify-vs-live``, ``committed:may-lag-uncommitted``,
#     ``learned:judge-applicability``). Self-safe; the label is never false.
#   - FACT-ASSERTION — claims an established positive fact (``live-oracle``).
#     Honest ONLY if an external check established it; injected every turn with
#     machine authority, it suppresses the verify instinct instead of inviting
#     it. A fact-assertion derived by construction launders a guess into
#     authority — more dangerous than no label.
# render_radar_block REFUSES to emit a fact-assertion without ``verified=True``,
# downgrading it to its honest unverified form: the tool cannot assert an
# unchecked fact. Same shape as the 26-6-14 reachable-state feeder — a fact
# defaults to its fail-closed "not established" form until an external event
# proves it (distinguish measured-negative from never-measured).
VERIFY_INVITATION = "verify_invitation"
FACT_ASSERTION = "fact_assertion"

TRUST_REGISTRY = {
    "cached:verify-vs-live": VERIFY_INVITATION,          # schema.sql dump
    "committed:may-lag-uncommitted": VERIFY_INVITATION,  # gitnexus / code
    "learned:judge-applicability": VERIFY_INVITATION,    # skills / doctrine
    "live-oracle": FACT_ASSERTION,                       # protocol post-promote
}

# A fact-assertion that failed (or skipped) its external check falls back to its
# honest verify-invitation form. Absent from this map → the generic "unverified".
_UNVERIFIED_DOWNGRADE = {
    "live-oracle": "live:unverified",
}


def render_radar_block(
    body: str, *, source: str, trust: str, verified: bool | None = None
) -> str:
    """Wrap one corpus's matched content in the shared provenance block
    (plan thj/26-6-16). Every radar corpus — skill, schema, doctrine,
    protocol — renders through this single shape so a stacked injection is
    legible: each block declares WHERE it came from and HOW MUCH to trust it.

    - ``source`` — ``<category>:<pointer>``; the prefix carries the corpus
      (``skill:`` / ``schema:`` / ``doctrine:`` / ``protocol:``). The pointer
      MUST be agent-legible (a grep-able key / path / component_key, never a
      UUID) so ``trust=…:verify`` is actionable.
    - ``trust`` — the source-currency posture, classified by ``TRUST_REGISTRY``
      into verify-invitation vs fact-assertion (see above).
    - ``verified`` — REQUIRED when ``trust`` is a fact-assertion: pass ``True``
      only when the external check (e.g. the protocol freshness watermark)
      confirmed the fact. Absent/``False``, a fact-assertion is DOWNGRADED to
      its unverified form — the block can never assert an unchecked fact.
      Verify-invitations ignore ``verified`` (they need no external check).

    NO ``score`` attribute by design: match confidence stays retrieval-side so
    an LLM can't cross-rank corpora whose scores aren't comparable. The body is
    emitted verbatim (it's LLM display context, not re-parsed XML); only the
    attribute values are escaped, keeping the opening tag well-formed.
    """
    if TRUST_REGISTRY.get(trust) == FACT_ASSERTION and verified is not True:
        trust = _UNVERIFIED_DOWNGRADE.get(trust, "unverified")
    return f'<radar source="{_attr(source)}" trust="{_attr(trust)}">\n{body}\n</radar>'
