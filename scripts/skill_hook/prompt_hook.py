"""
Claude Code / Codex UserPromptSubmit hook — semantic skill suggestion on prompt intent.

Fires before Claude processes each user message. Embeds the prompt and finds
the closest matching skill chunks in the bifurcated Skill Radar indexes. Surfaces a
skill section at the "perfect moment" — when Claude is about to strategize
on a fix, pick a direction, or answer a domain question.

Design choices (differ from hook.py):
- Higher threshold (0.72) — prompts are chattier than error strings, false
  positives are more annoying because they fire every turn.
- Skip trivial prompts (< 30 chars, sentinel messages) — nothing to retrieve
  against meaningfully.
- Top 1 match only by default — prompts are usually single-intent; two matches
  doubles the noise surface without doubling signal.
- No logging to SKILL_DEBT.md (that file is for error-time coverage gaps).
  DOES log injections to SKILL_INJECT_LOG.md so Outcome C (false positives)
  stays detectable.

Exits silently (code 0) on any failure — never blocks the agent runtime.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Local import — Skill Radar embed client, backed by the utilities ONNX service.
sys.path.insert(0, str(Path(__file__).parent))
from embed_client import embed as _shared_embed
from event_adapter import PromptEvent, normalize_event
from output_adapter import render_additional_context

INDEX_WISDOM_PATH = Path.home() / ".claude/skill_index_wisdom.json"
INDEX_WHAT_PATH = Path.home() / ".claude/skill_index_what.json"
SKILL_INJECT_LOG_PATH = Path.home() / "repo_docs/skills/SKILL_INJECT_LOG.md"
QUERY_PREFIX = "search_query: "

# Bifurcated radar: two content classes, two distributions, two thresholds.
# - WISDOM (Layers 1+4): narrative, high semantic signal, conservative bar.
# - WHAT  (Layer 3 cluster digests): structural tables/symbol lists, lower
#   semantic signal against natural-language prompts.
# WHAT raised from 0.65 → 0.72 on 2026-05-13 after SKILL_INJECT_LOG analysis
# showed cluster-digest chunks (`<cluster> › Entry Points / How to Explore /
# Key Files`) over-firing in the 0.65-0.71 band — identifier-soup content
# embeds broadly against most project prompts. Aligning to the wisdom bar
# preserves high-confidence cluster matches and drops the noisy near-threshold
# fires that don't teach anything the routing table doesn't already say.
THRESHOLD_WISDOM = 0.72
THRESHOLD_WHAT = 0.72

# Top-1 from each dimension: prompts are usually single-intent on each axis.
# Two surfaces firing at once is fine; two within one surface doubles noise.
TOP_PER_DIM = 1

CONTEXT_CHARS = 800
INJECT_SNIPPET_CHARS = 200
MIN_PROMPT_CHARS = 30

SKIP_PATTERNS = re.compile(
    r"^(yes|no|ok|okay|sure|thanks|thank you|continue|go|do it|proceed|"
    r"__greeting__|__check_in__)\b",
    re.IGNORECASE,
)

# Minimum length for a keyword trigger phrase to avoid false positives
# on short common words that happen to appear in Load When text.
MIN_TRIGGER_LEN = 4


def extract_triggers(index: list[dict]) -> list[tuple[str, dict]]:
    """Build (lowercase_phrase, best_chunk) pairs from Load When keywords.

    Extracts:
    1. Quoted phrases from Load When text (e.g., "quick take")
    2. Skill names themselves (e.g., "gitnexus")

    Returns longest-first so "quick take" matches before "quick".
    """
    # Group chunks by skill_name — we'll inject the first chunk per skill
    by_skill: dict[str, dict] = {}
    triggers: dict[str, str] = {}  # phrase → skill_name

    for entry in index:
        sname = entry.get("skill_name", "")
        if sname and sname not in by_skill:
            by_skill[sname] = entry

        load_when = entry.get("load_when", "")
        if not load_when:
            continue

        # Extract quoted phrases: "quick take", "review this", etc.
        for m in re.finditer(r'"([^"]+)"', load_when):
            phrase = m.group(1).strip().lower()
            if len(phrase) >= MIN_TRIGGER_LEN:
                triggers[phrase] = sname

    # Add skill names as triggers
    for sname in by_skill:
        if len(sname) >= MIN_TRIGGER_LEN:
            triggers[sname.lower()] = sname

    # Build (phrase, chunk) pairs, longest-first
    pairs = []
    for phrase, sname in sorted(triggers.items(), key=lambda t: -len(t[0])):
        if sname in by_skill:
            pairs.append((phrase, by_skill[sname]))
    return pairs


def keyword_prefilter(prompt: str, index: list[dict]) -> list[dict] | None:
    """If the prompt contains a registered trigger phrase, return matching
    chunks directly — no embedding needed. Returns None if no match."""
    prompt_lower = prompt.lower()
    triggers = extract_triggers(index)

    for phrase, chunk in triggers:
        if phrase in prompt_lower:
            return [{"score": 1.0, **chunk}]
    return None


def dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def embed(text: str) -> list[float] | None:
    """Single-text embed via shared-svcs. Returns None on any failure so the
    hook silently no-ops instead of blocking Claude Code."""
    try:
        return _shared_embed([text], timeout=3.0)[0]
    except Exception:
        return None


def load_index(path: Path) -> list[dict]:
    """Load one dimension's index. Empty list on any failure (silent no-op
    discipline — never block Claude Code on a missing/malformed cache)."""
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def log_skill_inject(prompt_text: str, matches: list[dict]) -> None:
    """Append an inject entry to SKILL_INJECT_LOG.md — shared with hook.py."""
    try:
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        snippet = prompt_text[:INJECT_SNIPPET_CHARS].replace("```", "~~~")

        match_lines = []
        for m in matches:
            skill = m.get("skill_name", m.get("name", "?"))
            header = m.get("header", "")
            match_lines.append(f"  {m['score']:.2f}  {skill} › {header}")

        entry = (
            f"\n## {ts} [prompt]\n\n"
            f"**Injected ({len(matches)} match{'es' if len(matches) != 1 else ''}):**\n"
            f"{chr(10).join(match_lines)}\n\n"
            f"**Prompt snippet:**\n"
            f"```\n{snippet}\n```\n\n"
            f"---\n"
        )

        marker = "<!-- Entries appended by hook.py — most recent at top -->"

        if not SKILL_INJECT_LOG_PATH.exists():
            return  # let hook.py create it first

        content = SKILL_INJECT_LOG_PATH.read_text()
        if marker in content:
            content = content.replace(marker, marker + entry, 1)
        else:
            content = content + entry

        SKILL_INJECT_LOG_PATH.write_text(content)
    except Exception:
        pass


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw)
    except Exception:
        sys.exit(0)

    event = normalize_event(payload)
    if not isinstance(event, PromptEvent):
        sys.exit(0)

    prompt = event.prompt.strip()
    if len(prompt) < MIN_PROMPT_CHARS:
        sys.exit(0)

    if SKIP_PATTERNS.match(prompt):
        sys.exit(0)

    wisdom_idx = load_index(INDEX_WISDOM_PATH)
    what_idx = load_index(INDEX_WHAT_PATH)
    if not wisdom_idx and not what_idx:
        sys.exit(0)

    # Tier 1: deterministic keyword match — fires on either dimension.
    # Search the union; if the matched chunk is in the what index, it goes
    # to the "what" surface, otherwise wisdom. Score is 1.0 either way.
    matches_by_dim: dict[str, list[dict]] = {"wisdom": [], "what": []}
    kw_matches = keyword_prefilter(prompt, wisdom_idx + what_idx)
    if kw_matches:
        # Bucket each match by which index it came from
        what_paths = {(e.get("file_path"), e.get("skill_name")) for e in what_idx}
        for m in kw_matches:
            key = (m.get("file_path"), m.get("skill_name"))
            dim = "what" if key in what_paths else "wisdom"
            matches_by_dim[dim].append(m)
    else:
        # Tier 2: semantic — single embed, score against each index, apply
        # per-dimension threshold, take top-N from each.
        query_vec = embed(QUERY_PREFIX + prompt[:1000])
        if not query_vec:
            sys.exit(0)

        for dim, idx, threshold in (
            ("wisdom", wisdom_idx, THRESHOLD_WISDOM),
            ("what", what_idx, THRESHOLD_WHAT),
        ):
            if not idx:
                continue
            scored = sorted(
                [{"score": dot(query_vec, s["embedding"]), **s} for s in idx],
                key=lambda x: x["score"],
                reverse=True,
            )
            matches_by_dim[dim] = [s for s in scored[:TOP_PER_DIM] if s["score"] >= threshold]

    all_matches = matches_by_dim["wisdom"] + matches_by_dim["what"]
    if not all_matches:
        sys.exit(0)

    log_skill_inject(prompt, all_matches)

    # Render side-by-side: one labeled section per dimension that fired.
    lines: list[str] = []
    section_labels = {
        "wisdom": "Skill Radar — what we've learned (Layers 1+4):",
        "what":   "Skill Radar — what is (Layer 3, project clusters):",
    }
    for dim in ("wisdom", "what"):
        ms = matches_by_dim[dim]
        if not ms:
            continue
        lines.append(section_labels[dim])
        lines.append("")
        for m in ms:
            skill = m.get("skill_name", m.get("name", "?"))
            header = m.get("header", "")
            fpath = m.get("file_path", "")
            score = m["score"]
            text = m.get("text", m.get("description", ""))
            lines.append(f"[{score:.2f}] {skill} › {header}  ({fpath})")
            lines.append("---")
            lines.append(text[:CONTEXT_CHARS])
            lines.append("")

    print(render_additional_context(
        "\n".join(lines),
        hook_event_name=event.hook_event_name,
        runtime=event.runtime,
    ))


if __name__ == "__main__":
    main()
