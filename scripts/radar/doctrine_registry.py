"""Doctrine Radar — parse + embed DOCTRINE_REGISTRY.md, match against prompts.

Each project's doctrine registry lives at
`<cwd>/symlink_docs/registries/DOCTRINE_REGISTRY.md` (or
`<cwd>/DOCTRINE_REGISTRY.md` as a fallback). The file format: H2
`## Rule: <title>` headings, each followed by bolded fields `**Source**:`,
`**Receipt**:`, `**Why**:`, `**Touchpoints**:`, `**Enforcement**:`. See the
canonical file at `~/repos/thj/symlink_docs/registries/DOCTRINE_REGISTRY.md`
for the canonical shape.

Renamed from `doctrine_index` to `doctrine_registry` 2026-05-27 to align
with the broader `registries/` lookup-table doctrine (RAILWAY_REGISTRY,
SCRIPT_REGISTRY) — flat, machine-grep-able lookup tables consulted by
need, not pre-loaded doctrine prose.

Phase 1 responsibility:
1. Parse the file into structured rule dicts.
2. Maintain an embedding cache at
   `~/.claude/doctrine_registry_<cwd-slug>.json`, content-hashed so we
   rebuild only when the doctrine registry changes.
3. Match a prompt against the rules (top-1 with score ≥ threshold).

Silent no-op discipline: any parse / embed / cache failure returns an empty
result — the doctrine radar never blocks Claude Code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from embed_client import embed as _shared_embed
from session_log import _slugify_cwd
from output_adapter import render_radar_block
import thresholds as th

# Doctrine match bar lives in the central `thresholds` module (th.DOCTRINE =
# 0.78, above the 0.72 skill bar; full calibration rationale in its Origin block).
DOCTRINE_MATCH_THRESHOLD = th.DOCTRINE

# H2 boundary — each rule starts with `## Rule: <title>` and runs until the
# next `## ` heading or EOF.
_RULE_HEADING_RE = re.compile(
    r"^## Rule:\s*(.+?)$(.+?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)

# Field extraction within a rule body. Each `**Field**:` runs until the next
# bolded field marker or end-of-rule. DOTALL so multi-line values capture.
_FIELD_LABELS = ("Source", "Receipt", "Why", "Touchpoints", "Enforcement")
_FIELD_RE = re.compile(
    r"^\*\*(" + "|".join(_FIELD_LABELS) + r")\*\*:\s*(.*?)(?=^\*\*(?:"
    + "|".join(_FIELD_LABELS) + r")\*\*:|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Strip `[text](url)` → `text` for cleaner field values.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def _doctrine_registry_paths(cwd: str | Path) -> list[Path]:
    """Candidate locations for DOCTRINE_REGISTRY.md in priority order.

    The canonical home is `symlink_docs/registries/DOCTRINE_REGISTRY.md`
    (the registries/ lane consolidates lookup-by-need flat tables —
    RAILWAY_REGISTRY, SCRIPT_REGISTRY, and now DOCTRINE_REGISTRY). The
    other candidates are backward-compat fallbacks:
      - `symlink_docs/project/DOCTRINE_INDEX.md` — pre-2026-05-27 layout
        where the file lived in project/ under its old INDEX name. Kept
        for projects that haven't migrated yet.
      - `DOCTRINE_REGISTRY.md` / `docs/DOCTRINE_REGISTRY.md` — alternate
        layouts for projects without symlink_docs.
    """
    cwd = Path(cwd)
    return [
        cwd / "symlink_docs" / "registries" / "DOCTRINE_REGISTRY.md",
        cwd / "symlink_docs" / "project" / "DOCTRINE_INDEX.md",  # legacy
        cwd / "DOCTRINE_REGISTRY.md",
        cwd / "DOCTRINE_INDEX.md",  # legacy
        cwd / "docs" / "DOCTRINE_REGISTRY.md",
    ]


def find_doctrine_registry(cwd: str | Path | None = None) -> Path | None:
    """Return the first matching DOCTRINE_REGISTRY.md path for this cwd, or
    None. Resolves symlinks so the cache hash reflects the real file."""
    cwd = cwd or os.getcwd()
    for candidate in _doctrine_registry_paths(cwd):
        if candidate.exists():
            try:
                return candidate.resolve()
            except Exception:
                return candidate
    return None


def _strip_markdown_links(text: str) -> str:
    return _MD_LINK_RE.sub(r"\1", text).strip()


def _parse_fields(body: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _FIELD_RE.finditer(body):
        label = match.group(1).lower()
        value = match.group(2).strip()
        out[label] = _strip_markdown_links(value)
    return out


def parse_doctrine_registry(path: Path) -> list[dict]:
    """Parse DOCTRINE_REGISTRY.md → list of rule dicts. Returns empty list on
    any read/parse failure. Each rule carries title + the bolded field
    values; downstream code embeds (title + why + receipt) and renders
    (title + source + receipt one-line)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    rules: list[dict] = []
    for match in _RULE_HEADING_RE.finditer(text):
        title = match.group(1).strip()
        body = match.group(2)
        fields = _parse_fields(body)
        rules.append({
            "title": title,
            "source": fields.get("source", ""),
            "receipt": fields.get("receipt", ""),
            "why": fields.get("why", ""),
            "touchpoints": fields.get("touchpoints", ""),
            "enforcement": fields.get("enforcement", ""),
        })
    return rules


def _content_hash(path: Path) -> str:
    """sha256 over the doctrine file's content. Cache invalidation key."""
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    return hashlib.sha256(data).hexdigest()[:16]


def _cache_path(cwd: str | Path) -> Path:
    """Per-project cache file. Slug-derived so multi-project sessions don't
    collide."""
    slug = _slugify_cwd(str(cwd))
    return Path.home() / ".claude" / f"radar_doctrine{slug}.json"


def _embed_text_for_rule(rule: dict) -> str:
    """Build the embedding text per rule. Title carries the rule name; why
    explains the mechanism; the first receipt line anchors the concrete
    incident. Bounded at ~600 chars to stay inside the embed model's
    effective signal range."""
    title = rule.get("title", "")
    why = (rule.get("why") or "")[:400]
    receipt_first_line = (rule.get("receipt") or "").split("\n", 1)[0][:200]
    parts = [p for p in (title, why, receipt_first_line) if p]
    return " | ".join(parts)


def _load_cache(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text())
    except Exception:
        return {}


def _save_cache(cache_path: Path, cache: dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False))
    except Exception:
        pass  # silent no-op


def load_doctrine_with_embeddings(cwd: str | Path | None = None) -> list[dict]:
    """Return the doctrine rules with embeddings attached.

    Cache lifecycle:
    - If the cache hash matches the current doctrine file hash, reuse cached
      embeddings verbatim (no embed service call).
    - Otherwise rebuild — embed each rule's `_embed_text_for_rule()` and save.
    - If the embed service is unavailable AND no cache exists, return [].
      If the embed service is unavailable AND a cache exists with a different
      hash, return the stale cache rather than blocking — better a near-miss
      than no signal.
    """
    cwd = cwd or os.getcwd()
    path = find_doctrine_registry(cwd)
    if not path:
        return []

    current_hash = _content_hash(path)
    cache_path = _cache_path(cwd)
    cache = _load_cache(cache_path)

    if cache.get("content_hash") == current_hash and cache.get("rules"):
        return cache["rules"]

    rules = parse_doctrine_registry(path)
    if not rules:
        return []

    # Try to embed each rule. On any failure, fall through to the stale cache
    # if present (better a near-miss than no signal).
    embed_failures = 0
    for rule in rules:
        text = _embed_text_for_rule(rule)
        if not text:
            rule["embedding"] = None
            continue
        try:
            rule["embedding"] = _shared_embed([text], timeout=3.0)[0]
        except Exception:
            rule["embedding"] = None
            embed_failures += 1

    # If everything failed and we have a stale cache, prefer the stale data.
    if embed_failures >= len(rules) and cache.get("rules"):
        return cache["rules"]

    _save_cache(
        cache_path,
        {"content_hash": current_hash, "rules": rules},
    )
    return rules


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def match_doctrine_for_prompt(
    prompt: str,
    *,
    cwd: str | Path | None = None,
    threshold: float = DOCTRINE_MATCH_THRESHOLD,
) -> dict | None:
    """Top-1 doctrine rule match for `prompt`. Returns the rule dict with
    `score` attached when score ≥ threshold, else None.

    Caller is responsible for the prompt-side embedding — we accept it as
    `prompt` text and embed inline here for symmetry with the rest of the
    radar module. Silent no-op on any failure."""
    if not prompt or not prompt.strip():
        return None
    rules = load_doctrine_with_embeddings(cwd)
    if not rules:
        return None
    try:
        prompt_vec = _shared_embed(["search_query: " + prompt[:1000]], timeout=3.0)[0]
    except Exception:
        return None
    best: dict | None = None
    best_score = -1.0
    for rule in rules:
        emb = rule.get("embedding")
        if not emb:
            continue
        score = dot(prompt_vec, emb)
        if score > best_score:
            best_score = score
            best = rule
    if not best or best_score < threshold:
        return None
    return {**best, "score": best_score}


def render_doctrine_section(rule: dict) -> str:
    """Render the doctrine match as a shared provenance block (plan
    thj/26-6-16 Phase 1). Single short stanza — doctrine matches are
    higher-stakes than skill matches, so the body prioritizes the rule
    statement + source + receipt one-liner over verbose context.

    `source="doctrine:<title>"` is agent-legible (grep-able in
    DOCTRINE_REGISTRY.md); `trust="learned:judge-applicability"` — doctrine,
    like skills, is learned guidance whose applicability the agent judges. The
    match score is deliberately NOT shown (it stays retrieval-side)."""
    title = rule.get("title", "")
    rule_source = rule.get("source", "")
    receipt = (rule.get("receipt") or "").split("\n", 1)[0]
    lines = [f"Rule: {title}"]
    if rule_source:
        lines.append(f"Source: {rule_source}")
    if receipt:
        lines.append(f"Receipt: {receipt}")
    return render_radar_block(
        "\n".join(lines),
        source=f"doctrine:{title}",
        trust="learned:judge-applicability",
    )
