"""
Protocol-component corpus for the radar (plan thj/26-6-16 Phase 2).

Transforms live ``protocol_component`` rows into embeddable chunks — one per
*section* — so ambient protocol awareness surfaces the talking points, coaching
voice, and triage thresholds where the chat-pathway truth actually lives.

Pure transformation + index/manifest path helpers. The DB read (``DATABASE_URL``)
lives in ``build_protocol_index.py``; this module never touches a DB or the
network, so it stays unit-testable.

Granularity (plan § Design, decision c — *index ALL units, never filter by
render-status*, the avoid-drop trap). A component yields:
  - one **content** chunk: ``title`` + ``content_goal`` + ``talking_points``
    (these co-render as the grounded bullets), and
  - one **governance** chunk per *substantive* ``clinical_patterns`` sub-key
    EXCEPT ``talking_points`` (coaching voice, triage thresholds, escalation
    copy, classifier criteria …).
``source`` is ``protocol:<component_key>`` (the stable cross-environment
identity, agent-legible); the ``section`` name rides in the chunk for the future
render-status annotation (Phase 2b). Thin/structural values (a bare threshold,
a boolean) fall below ``MIN_SECTION_CHARS`` and are skipped — the corpus is
self-limiting to sections carrying durable semantic context.
"""

from __future__ import annotations

import json
from pathlib import Path

# A section below this is too thin to carry durable semantic context (a bare
# `severity: high` / `min_weeks: 6`); skip it. Governance prose, talking-point
# blocks, and content goals clear it comfortably.
MIN_SECTION_CHARS = 30
# Truncate a rendered chunk before embedding/injection.
MAX_CHUNK_CHARS = 1500

# Bump when the parse/render shape changes so a stale index rebuilds.
CONFIG_SIGNATURE = "protocol-v1|min=30|max=1500|prefix=search_document|gran=section"

# clinical_patterns keys folded into the CONTENT chunk, not emitted as their own
# governance section (they co-render with title/content_goal as patient bullets).
_CONTENT_CP_KEYS = {"talking_points"}


def _flatten(value) -> str:
    """Render a JSONB value (str | list | dict | scalar) into readable lines."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(
            f"- {_flatten(v)}" for v in value if v not in (None, "")
        )
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            sub = _flatten(v)
            if not sub:
                continue
            lines.append(f"{k}: {sub}" if "\n" not in sub else f"{k}:\n{sub}")
        return "\n".join(lines)
    return str(value)


def _content_text(title: str, content_goal, cp: dict) -> str:
    """The component's patient-facing content unit: title + goal + talking points."""
    parts: list[str] = []
    if title:
        parts.append(str(title).strip())
    if content_goal:
        parts.append(f"Goal: {str(content_goal).strip()}")
    tps = cp.get("talking_points")
    if isinstance(tps, list) and tps:
        parts.append("\n".join(f"- {t}" for t in tps if t))
    return "\n".join(parts).strip()


def build_chunks_from_rows(rows: list[dict]) -> list[dict]:
    """``rows``: ``[{component_key, protocol_id, title, content_goal, clinical_patterns}]``
    (``clinical_patterns`` a dict or JSON string). Returns sorted chunk dicts:
    ``{component_key, protocol_id, section, title, text}`` — ``section`` is
    ``None`` for the content chunk, the sub-key name for governance chunks."""
    chunks: list[dict] = []
    for r in rows:
        ck = r.get("component_key") or ""
        if not ck:
            continue
        pid = r.get("protocol_id") or ""
        title = r.get("title") or ""
        cp = r.get("clinical_patterns")
        if isinstance(cp, str):
            try:
                cp = json.loads(cp)
            except Exception:
                cp = {}
        if not isinstance(cp, dict):
            cp = {}

        content = _content_text(title, r.get("content_goal"), cp)
        if len(content) >= MIN_SECTION_CHARS:
            chunks.append({
                "component_key": ck, "protocol_id": pid, "section": None,
                "title": title, "text": content[:MAX_CHUNK_CHARS],
            })

        for key, val in cp.items():
            if key in _CONTENT_CP_KEYS:
                continue
            body = _flatten(val)
            if len(body) < MIN_SECTION_CHARS:
                continue
            text = f"{title} § {key}\n{body}" if title else f"{key}\n{body}"
            chunks.append({
                "component_key": ck, "protocol_id": pid, "section": key,
                "title": title, "text": text[:MAX_CHUNK_CHARS],
            })

    chunks.sort(key=lambda c: (c["component_key"], c["section"] or ""))
    return chunks


# ── Per-repo index paths (slug-keyed, sibling to the schema corpus) ───────────

def index_path(slug: str) -> Path:
    return Path.home() / ".claude" / f"radar_protocol_{slug}.json"


def manifest_path(slug: str) -> Path:
    return Path.home() / ".claude" / f"radar_protocol_{slug}_manifest.json"
