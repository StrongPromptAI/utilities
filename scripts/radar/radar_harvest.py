"""harvest.py — weekly skill-queue harvest.

Reads session-log.jsonl across every project, plus brief candidate checkboxes
under `~/repo_docs/<project>/briefs/sessions/` (the dual-radar harvest input
lane — sister lanes `reviews/`, `responses/`, `designs/`, `logs/` are NOT
harvested; they have different cadence and audience). Falls back to
`~/repo_docs/<project>/briefs/` flat layout for projects that haven't yet
reorganized into lanes. Bucketizes by error fingerprint and appends new
unique candidates to `~/repo_docs/skills/SKILL_QUEUE.md`.

Per the skill-lifecycle refactor plan (and GLM 5.1 quick-take 2026-05-26),
this script does NOT mechanize the Three Categories Test. It surfaces raw
deduplicated candidates with evidence; the human applies the test at promotion.

Run manually:
    uv run --project ~/repos/utilities python ~/repos/utilities/scripts/radar/radar_harvest.py

Or scheduled (Phase 4) — weekly Sunday 09:00 local.

Exit codes:
    0 — success (queue updated or no new candidates)
    1 — failure (file unreadable, queue write blocked)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

QUEUE_PATH = Path.home() / "repo_docs" / "skills" / "SKILL_QUEUE.md"
HARVEST_HEARTBEAT = Path.home() / ".claude" / "last-skill-harvest.txt"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
REPO_DOCS_DIR = Path.home() / "repo_docs"

# Persistent "already emitted" ledger. The queue-only dedup (_existing_queue_keys)
# can't see candidates that were PROMOTED or REJECTED (deleted from the queue),
# so within the lookback window the harvest re-emits them every run — the
# unchecked-checkbox replay that resurfaced 7 already-promoted candidates on
# 2026-05-28. This ledger records every fingerprint the harvest has ever
# emitted; once a fingerprint is here it never re-emits, with zero manual
# action at promotion time. Append-only, one fingerprint per line.
SEEN_LEDGER = Path.home() / ".claude" / "harvest-seen-fingerprints.txt"

# How far back to look in each session-log for new candidates. Rows older
# than this were already harvested in a prior cycle and would dedupe-collide
# with existing queue entries, but skipping them up front saves work.
LOOKBACK_DAYS = 14

# Skill candidates checkbox pattern in briefs — matches the canonical template
# `- [ ] **Candidate**: <text>. **Category**: ...`
BRIEF_CANDIDATE_RE = re.compile(
    r"^- \[ \] \*\*Candidate\*\*: (.+?)\. \*\*Category\*\*: (.+?)\. "
    r"\*\*Target skill\*\*: (.+?)\. \*\*Evidence\*\*: (.+?)$",
    re.MULTILINE,
)

# Phase 3 — doctrine catches section in briefs. The canonical template renders
# each catch as a multi-line `- **Rule**: ... **Source**: ... **What happened**:
# ... **Fix**: ... **Receipt**: ...` bullet under the H2 heading.
DOCTRINE_SECTION_RE = re.compile(
    r"^## Doctrine Violations Caught in Review\s*$(.+?)(?=^## |\Z)",
    re.MULTILINE | re.DOTALL,
)

# Each catch bullet starts with `- **Rule**:` and contains the labeled fields.
# We tolerate placeholder lines (`<rule title verbatim ...>`) from a
# not-yet-filled-in template by checking for angle-bracket placeholder text.
DOCTRINE_BULLET_RE = re.compile(
    r"^- \*\*Rule\*\*:\s*(?:`)?([^`\n]+?)(?:`)?\s*$"
    r"(?:\s+\*\*Source\*\*:\s*(?:`)?([^`\n]*?)(?:`)?\s*$)?"
    r"(?:\s+\*\*What happened\*\*:\s*(.+?)\s*$)?"
    r"(?:\s+\*\*Fix\*\*:\s*(.+?)\s*$)?"
    r"(?:\s+\*\*Receipt\*\*:\s*(.+?)\s*$)?",
    re.MULTILINE | re.DOTALL,
)

PLACEHOLDER_RE = re.compile(r"<[^>]+>")

# --- Bash-error noise filter (skill-track) -----------------------------------
# A non-zero bash exit is logged as `bash_error`, but most are exploratory or
# control-flow, not a knowledge gap worth a skill: an existence check that
# returns "not found", a typo'd command, a one-off psql/python error hit while
# debugging. Without this filter every such row became a queue candidate — the
# 267-entry backlog that triggered this fix (2026-06-07). Two guards:
#   (1) MISS patterns — file/command "not found": an existence check, never a wall.
#   (2) Recurrence floor — a single occurrence (count < 2) is an exploration, not
#       a repeated stumbling block. DEFERRED, not suppressed: a one-off whose
#       fingerprint isn't ledgered re-surfaces from a later harvest once it
#       recurs (count >= 2). NB: skill_match.score is deliberately NOT used here
#       — empirically it lands 0.5-0.9 for *every* error (the embedder always
#       finds some nearby skill chunk), so it can't discriminate noise. The
#       repeat count is the signal: it tracks how many times Claude actually hit
#       the wall. Human-judgment one-offs go through brief candidates, not here.
_BASH_MISS_RE = re.compile(
    r"no such file or directory"
    r"|command not found"
    r"|: not found\b"
    r"|not a directory",
    re.IGNORECASE,
)


def _is_nonskill_bash_noise(err_text: str, count: int) -> bool:
    """True when a logged error bucket is exploratory noise, not a skill candidate."""
    if _BASH_MISS_RE.search(err_text or ""):
        return True
    if count < 2:  # one-off exploration, not a repeated stumbling block
        return True
    return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _touch_harvest_heartbeat() -> None:
    try:
        HARVEST_HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HARVEST_HEARTBEAT.write_text(_now_iso() + "\n")
    except Exception:
        pass


def _load_seen() -> set[str]:
    """Load the already-emitted fingerprint ledger. Empty set if missing.

    Fingerprints are stored verbatim — a `[:60]` truncation can legitimately
    end on a space, so do NOT strip the line content (splitlines already drops
    the newline); stripping would break the round-trip with the computed fp.
    """
    if not SEEN_LEDGER.exists():
        return set()
    return {
        line
        for line in SEEN_LEDGER.read_text().splitlines()
        if line.strip()
    }


def _append_seen(fingerprints: set[str]) -> None:
    """Append new fingerprints to the ledger (append-only; dedup on read)."""
    if not fingerprints:
        return
    try:
        SEEN_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with SEEN_LEDGER.open("a") as fh:
            for fp in sorted(fingerprints):
                fh.write(fp + "\n")
    except Exception:
        pass


def _project_name_from_slug(slug: str) -> str:
    """Decode a project dir slug back to a human label.
    `-Users-metatronfly-repos-thj` → `thj`
    `-Users-metatronfly-repo-docs` → `repo-docs`"""
    parts = slug.lstrip("-").split("-")
    # Drop the leading 'Users/<user>/repos/' (or 'repo-docs/') prefix
    if len(parts) >= 4 and parts[0] == "Users" and parts[2] == "repos":
        return "-".join(parts[3:]) or slug
    if len(parts) >= 3 and parts[0] == "Users" and parts[2] == "repo":
        return "-".join(parts[3:]) or slug
    return slug


def _read_jsonl_rows(log_path: Path, since: datetime) -> list[dict]:
    """Stream-parse a session-log.jsonl, return rows newer than `since`.
    Silent no-op on parse error (skips that row)."""
    if not log_path.exists():
        return []
    rows: list[dict] = []
    try:
        with log_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = row.get("ts")
                if not ts:
                    continue
                try:
                    row_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
                except Exception:
                    continue
                if row_dt < since:
                    continue
                rows.append(row)
    except Exception:
        return []
    return rows


def _bucketize_rows(rows: list[dict]) -> dict[str, dict]:
    """Group rows by dedupe_key. For each bucket, capture: count, last_seen ts,
    representative error_text, top skill_match (if any), event types observed."""
    buckets: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "last_seen": "",
        "first_seen": "",
        "event_types": set(),
        "error_text": "",
        "command_or_context": "",
        "skill_match": None,
        "outcomes": set(),
    })
    for row in rows:
        key = row.get("dedupe_key") or ""
        if not key:
            continue
        b = buckets[key]
        b["count"] += 1
        ts = row.get("ts", "")
        if not b["first_seen"] or ts < b["first_seen"]:
            b["first_seen"] = ts
        if not b["last_seen"] or ts > b["last_seen"]:
            b["last_seen"] = ts
        b["event_types"].add(row.get("event_type", ""))
        b["outcomes"].add(row.get("outcome", ""))
        if not b["error_text"] and row.get("error_text"):
            b["error_text"] = row["error_text"]
        if not b["command_or_context"] and row.get("command_or_context"):
            b["command_or_context"] = row["command_or_context"]
        if not b["skill_match"] and row.get("skill_match"):
            b["skill_match"] = row["skill_match"]
    return buckets


def _parse_brief_candidates(briefs_dir: Path) -> list[dict]:
    """Read each *.md brief in `briefs_dir` (excluding _TEMPLATE.md) and
    extract `- [ ]` candidate checkboxes via BRIEF_CANDIDATE_RE."""
    out: list[dict] = []
    if not briefs_dir.exists():
        return out
    for brief_file in briefs_dir.iterdir():
        if not brief_file.is_file() or brief_file.suffix != ".md":
            continue
        if brief_file.name == "_TEMPLATE.md":
            continue
        try:
            text = brief_file.read_text()
        except Exception:
            continue
        for m in BRIEF_CANDIDATE_RE.finditer(text):
            candidate, category, target, evidence = m.groups()
            out.append({
                "source_brief": str(brief_file),
                "candidate": candidate.strip(),
                "category": category.strip(),
                "target": target.strip(),
                "evidence": evidence.strip(),
            })
    return out


def _parse_brief_doctrine_catches(briefs_dir: Path) -> list[dict]:
    """Phase 3 — extract doctrine catches from each brief's "## Doctrine
    Violations Caught in Review" section. Skips bullets whose Rule field is
    still a template placeholder (`<...>`). Returns one entry per real bullet."""
    out: list[dict] = []
    if not briefs_dir.exists():
        return out
    for brief_file in briefs_dir.iterdir():
        if not brief_file.is_file() or brief_file.suffix != ".md":
            continue
        if brief_file.name == "_TEMPLATE.md":
            continue
        try:
            text = brief_file.read_text()
        except Exception:
            continue
        section_match = DOCTRINE_SECTION_RE.search(text)
        if not section_match:
            continue
        section_body = section_match.group(1)
        for m in DOCTRINE_BULLET_RE.finditer(section_body):
            rule = (m.group(1) or "").strip()
            if not rule or PLACEHOLDER_RE.search(rule):
                continue  # template placeholder, not a real catch
            source = (m.group(2) or "").strip()
            what_happened = (m.group(3) or "").strip()
            fix = (m.group(4) or "").strip()
            receipt = (m.group(5) or "").strip()
            # Drop entries where everything is still a placeholder.
            if all(
                not v or PLACEHOLDER_RE.match(v)
                for v in (source, what_happened, fix, receipt)
            ):
                continue
            out.append({
                "source_brief": str(brief_file),
                "rule": rule,
                "source": source,
                "what_happened": what_happened,
                "fix": fix,
                "receipt": receipt,
            })
    return out


def _bucketize_doctrine_rows(rows: list[dict]) -> dict[str, dict]:
    """Group doctrine_match rows by (rule, outcome). Only manual rows with
    outcome='caught_in_review' are promotion-track — auto-fires (the
    prompt_hook radar) are observability signal, not queue candidates."""
    buckets: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "last_seen": "",
        "first_seen": "",
        "rule": "",
        "rule_source": "",
        "receipt": "",
        "evidence": "",
        "touchpoint": [],
        "match_type": "",
    })
    for row in rows:
        if row.get("event_type") != "doctrine_match":
            continue
        if row.get("match_type") != "manual":
            continue
        if row.get("outcome") != "caught_in_review":
            continue
        rule = (row.get("rule") or "").strip()
        if not rule:
            continue
        key = f"doctrine::{rule.lower()[:120]}"
        b = buckets[key]
        b["count"] += 1
        ts = row.get("ts", "")
        if not b["first_seen"] or ts < b["first_seen"]:
            b["first_seen"] = ts
        if not b["last_seen"] or ts > b["last_seen"]:
            b["last_seen"] = ts
        b["rule"] = rule
        if not b["rule_source"]:
            b["rule_source"] = row.get("rule_source", "")
        if not b["receipt"]:
            b["receipt"] = row.get("receipt", "")
        if not b["evidence"]:
            b["evidence"] = row.get("evidence", "")
        tp = row.get("touchpoint") or []
        if isinstance(tp, list):
            for t in tp:
                if t and t not in b["touchpoint"]:
                    b["touchpoint"].append(t)
        b["match_type"] = "manual"
    return buckets


def _existing_queue_keys(queue_text: str) -> tuple[set[str], set[str]]:
    """Extract coarse fingerprint sets from the existing queue so we don't
    re-add candidates already present.

    Returns (oneliner_keys, dedupe_keys) — two independent identity surfaces.
    oneliner_keys: first ~60 chars of each One-liner value.
    dedupe_keys: sha256 dedupe keys already in the queue."""
    oneliner_keys: set[str] = set()
    dedupe_keys: set[str] = set()
    for line in queue_text.splitlines():
        if line.startswith("**One-liner**:"):
            fingerprint = line.split(":", 1)[1].strip()[:60].lower()
            oneliner_keys.add(fingerprint)
        elif line.startswith("**Dedupe key**: `"):
            dk = line.split("`")[1]
            dedupe_keys.add(dk)
    return oneliner_keys, dedupe_keys


def _format_jsonl_candidate(project: str, key: str, bucket: dict) -> str | None:
    """Emit a Markdown candidate entry for a JSONL-derived bucket.
    Returns None when the bucket should not become a candidate (e.g., grep
    violations and clean prompt_match injections — those are precision
    signals, not skill candidates)."""
    event_types = bucket["event_types"]
    if event_types == {"grep_on_code"}:
        return None  # tool-protocol redirect, not a skill candidate
    if event_types == {"prompt_match"} and "missed" not in bucket["outcomes"]:
        return None  # successful injection — radar already firing
    if event_types == {"doctrine_match"}:
        return None  # doctrine rows go to the doctrine section, not skill

    skill_match = bucket["skill_match"]
    if _is_nonskill_bash_noise(
        bucket["error_text"] or bucket["command_or_context"] or "",
        bucket.get("count", 1),
    ):
        return None  # exploratory/transient bash noise — not a skill candidate

    err = bucket["error_text"] or bucket["command_or_context"] or "(no text)"
    err_one_line = re.sub(r"\s+", " ", err.strip())[:200]
    target = (
        f"existing skill `{skill_match['skill']}` (close miss, score {skill_match['score']:.2f})"
        if skill_match and skill_match.get("score", 0) > 0.5
        else "NEW skill OR existing — needs disambiguation"
    )

    today = _now_iso()[:10]
    return (
        f"\n## {today} — [{project}] {err_one_line[:80]}\n"
        f"\n"
        f"**Category**: gotcha (auto-classified; review at promotion)\n"
        f"**Target skill**: {target}\n"
        f"**Evidence**: session-log.jsonl `{project}` — {bucket['count']} hit"
        f"{'s' if bucket['count'] != 1 else ''}, last seen {bucket['last_seen']}\n"
        f"**One-liner**: {err_one_line}\n"
        f"**Dedupe key**: `{key}`\n"
        f"\n"
        f"---\n"
    )


def _format_brief_candidate(c: dict) -> str:
    today = _now_iso()[:10]
    brief_name = Path(c["source_brief"]).name
    return (
        f"\n## {today} — [from {brief_name}] {c['candidate'][:80]}\n"
        f"\n"
        f"**Category**: {c['category']}\n"
        f"**Target skill**: {c['target']}\n"
        f"**Evidence**: brief `{c['source_brief']}` — {c['evidence']}\n"
        f"**One-liner**: {c['candidate']}\n"
        f"\n"
        f"---\n"
    )


def _format_doctrine_jsonl_candidate(project: str, bucket: dict) -> str:
    """Format a doctrine candidate harvested from session-log.jsonl
    (caught_in_review row written by record-session-event)."""
    today = _now_iso()[:10]
    rule = bucket["rule"]
    rule_source = bucket["rule_source"] or "(no source provided)"
    receipt = bucket["receipt"] or "(no receipt provided)"
    evidence_extra = bucket["evidence"] or ""
    touchpoints = ", ".join(bucket["touchpoint"]) if bucket["touchpoint"] else "(none)"
    evidence_line = (
        f"session-log.jsonl `{project}` — {bucket['count']} catch"
        f"{'es' if bucket['count'] != 1 else ''}, last seen {bucket['last_seen']}"
    )
    if evidence_extra:
        evidence_line += f"; {evidence_extra}"
    return (
        f"\n### {today} — {rule[:80]}\n"
        f"\n"
        f"**Rule**: {rule}\n"
        f"**Source**: {rule_source}\n"
        f"**Touchpoints**: {touchpoints}\n"
        f"**Evidence**: {evidence_line}\n"
        f"**Receipt**: {receipt}\n"
        f"**Promotion path**: Edit the source doc (`{rule_source}`) or "
        f"`DOCTRINE_REGISTRY.md`, then delete this entry.\n"
        f"\n"
        f"---\n"
    )


def _format_doctrine_brief_candidate(c: dict) -> str:
    today = _now_iso()[:10]
    brief_name = Path(c["source_brief"]).name
    receipt = c["receipt"] or "(see brief)"
    return (
        f"\n### {today} — [from {brief_name}] {c['rule'][:80]}\n"
        f"\n"
        f"**Rule**: {c['rule']}\n"
        f"**Source**: {c['source'] or '(no source provided)'}\n"
        f"**What happened**: {c['what_happened']}\n"
        f"**Fix**: {c['fix']}\n"
        f"**Receipt**: {receipt}\n"
        f"**Promotion path**: Edit the source doc (`{c['source']}`) or "
        f"`DOCTRINE_REGISTRY.md`, then delete this entry.\n"
        f"\n"
        f"---\n"
    )


def _existing_doctrine_keys(queue_text: str) -> set[str]:
    """Extract a coarse fingerprint set of doctrine rules already in the queue
    so we don't re-add. The fingerprint is the first 80 chars of each rule
    line (lowercased) — stable enough to dedupe re-harvests, loose enough to
    survive minor whitespace drift."""
    keys: set[str] = set()
    # Only scan from the "## Doctrine Candidates" heading onward so we don't
    # collide with skill-section "**Source**: ..." lines.
    doctrine_idx = queue_text.find("\n## Doctrine Candidates")
    if doctrine_idx < 0:
        return keys
    section = queue_text[doctrine_idx:]
    for line in section.splitlines():
        if line.startswith("**Rule**:"):
            fp = line.split(":", 1)[1].strip().lower()[:80]
            keys.add(fp)
    return keys


_QUEUE_BLOCK_RE = re.compile(
    r"^## \d{4}-\d{2}-\d{2} — \[.*?(?=^## \d{4}-\d{2}-\d{2} — \[|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _prune_queue(queue_text: str) -> tuple[str, int, int]:
    """Re-evaluate the EXISTING JSONL-sourced skill candidates against the noise
    filter and drop the exploratory ones — the one-time cleanup companion to the
    emit-time guard in `_format_jsonl_candidate`, using the same predicate.

    Brief-sourced candidates (`[from <brief>]`) and the entire Doctrine section
    are always kept. Returns (new_text, kept, dropped)."""
    s_idx = queue_text.find("## Skill Candidates")
    if s_idx < 0:
        return queue_text, 0, 0
    d_idx = queue_text.find("\n## Doctrine Candidates")
    if d_idx < 0:
        d_idx = len(queue_text)
    head, skill_section, tail = (
        queue_text[:s_idx], queue_text[s_idx:d_idx], queue_text[d_idx:],
    )

    first = _QUEUE_BLOCK_RE.search(skill_section)
    if not first:
        return queue_text, 0, 0
    preamble = skill_section[: first.start()]

    kept_blocks: list[str] = []
    kept = dropped = 0
    for m in _QUEUE_BLOCK_RE.finditer(skill_section):
        block = m.group(0)
        header = block.split("\n", 1)[0]
        if "[from " in header:  # human-authored brief candidate — always keep
            kept_blocks.append(block)
            kept += 1
            continue
        oneliner_m = re.search(r"^\*\*One-liner\*\*: (.+)$", block, re.MULTILINE)
        count_m = re.search(r"— (\d+) hit", block)
        oneliner = oneliner_m.group(1) if oneliner_m else ""
        count = int(count_m.group(1)) if count_m else 1
        if _is_nonskill_bash_noise(oneliner, count):
            dropped += 1
        else:
            kept_blocks.append(block)
            kept += 1

    return head + preamble + "".join(kept_blocks) + tail, kept, dropped


def main() -> int:
    p = argparse.ArgumentParser(prog="harvest.py")
    p.add_argument(
        "--prune-queue", action="store_true",
        help="One-time cleanup: re-evaluate existing JSONL skill candidates "
             "against the noise filter and drop the exploratory ones. Honors "
             "--dry-run. Brief candidates + the doctrine section are untouched.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be appended; don't write the queue file.",
    )
    p.add_argument(
        "--lookback-days", type=int, default=LOOKBACK_DAYS,
        help=f"How far back to scan session-log rows (default {LOOKBACK_DAYS}).",
    )
    p.add_argument(
        "--seed-ledger", action="store_true",
        help="Record every currently-computable candidate fingerprint into the "
             "seen-ledger WITHOUT touching the queue. Run once after a manual "
             "triage so already-triaged candidates never re-emit.",
    )
    args = p.parse_args()

    if args.prune_queue:
        queue_text = QUEUE_PATH.read_text() if QUEUE_PATH.exists() else ""
        new_text, kept, dropped = _prune_queue(queue_text)
        new_text = _update_queue_header(new_text, existing_only=False)
        if args.dry_run:
            print(f"# DRY RUN --prune-queue: would keep {kept}, drop {dropped} "
                  f"JSONL skill candidate(s).")
            return 0
        QUEUE_PATH.write_text(new_text)
        print(f"harvest --prune-queue: kept {kept}, dropped {dropped} noise candidate(s).")
        return 0

    cutoff = datetime.now(timezone.utc).astimezone().replace(
        microsecond=0,
    ).astimezone() - __import__("datetime").timedelta(days=args.lookback_days)

    if not PROJECTS_DIR.exists():
        print(f"no projects dir at {PROJECTS_DIR}", file=sys.stderr)
        return 1

    # 1. Collect JSONL buckets (skill-track) + doctrine buckets across all projects.
    all_buckets: dict[str, tuple[str, dict]] = {}  # skill key → (project, bucket)
    all_doctrine_buckets: dict[str, tuple[str, dict]] = {}  # doctrine key → (project, bucket)
    for proj_dir in PROJECTS_DIR.iterdir():
        if not proj_dir.is_dir():
            continue
        log_path = proj_dir / "session-log.jsonl"
        if not log_path.exists():
            continue
        project_name = _project_name_from_slug(proj_dir.name)
        rows = _read_jsonl_rows(log_path, cutoff)

        # Skill-track buckets
        buckets = _bucketize_rows(rows)
        for key, bucket in buckets.items():
            existing = all_buckets.get(key)
            if existing and existing[1]["count"] >= bucket["count"]:
                continue
            all_buckets[key] = (project_name, bucket)

        # Doctrine-track buckets — manual caught_in_review rows only
        doctrine_buckets = _bucketize_doctrine_rows(rows)
        for key, bucket in doctrine_buckets.items():
            existing = all_doctrine_buckets.get(key)
            if existing and existing[1]["count"] >= bucket["count"]:
                continue
            all_doctrine_buckets[key] = (project_name, bucket)

    # 2. Read brief candidates + doctrine catches across all projects in repo_docs/.
    brief_candidates: list[dict] = []
    brief_doctrine_catches: list[dict] = []
    if REPO_DOCS_DIR.exists():
        for proj_dir in REPO_DOCS_DIR.iterdir():
            # Brief lanes (introduced 2026-05-27): sessions/ is the dual-radar
            # harvest input. Reviews/responses/designs/logs are sister lanes
            # the harvest does NOT read (different cadence, different audience).
            # Backward-compat: fall through to briefs/ flat layout if sessions/
            # doesn't exist yet (the project hasn't reorganized).
            sessions = proj_dir / "briefs" / "sessions"
            briefs = sessions if sessions.exists() else proj_dir / "briefs"
            if briefs.exists():
                brief_candidates.extend(_parse_brief_candidates(briefs))
                brief_doctrine_catches.extend(_parse_brief_doctrine_catches(briefs))

    # 3. Load existing queue + existing fingerprints (skill + doctrine separately),
    #    plus the persistent seen-ledger (survives promotion/rejection deletes).
    queue_text = QUEUE_PATH.read_text() if QUEUE_PATH.exists() else ""
    existing_keys, existing_dedupe_keys = _existing_queue_keys(queue_text)
    existing_doctrine_keys = _existing_doctrine_keys(queue_text)
    seen = _load_seen()

    # Seed mode: record every currently-computable fingerprint into the ledger
    # WITHOUT touching the queue. Run once after a manual triage so already-
    # triaged candidates (promoted OR rejected) never re-emit.
    if args.seed_ledger:
        seed_fps: set[str] = set()
        seed_fps.update(all_buckets.keys())
        seed_fps.update(c["candidate"][:60].lower() for c in brief_candidates)
        for _proj, bucket in all_doctrine_buckets.values():
            fp = (bucket["rule"] or "").strip().lower()[:80]
            if fp:
                seed_fps.add(fp)
        seed_fps.update(c["rule"].strip().lower()[:80] for c in brief_doctrine_catches)
        new_fps = seed_fps - seen
        _append_seen(new_fps)
        print(f"harvest --seed-ledger: recorded {len(new_fps)} new fingerprint(s); "
              f"ledger now {len(seen | seed_fps)} total.")
        return 0

    # 4a. Build new skill candidate entries.
    new_entries: list[str] = []
    emitted_fps: set[str] = set()  # fingerprints appended this run → ledger
    new_from_jsonl = 0
    new_from_briefs = 0
    for key, (project_name, bucket) in all_buckets.items():
        if key in existing_dedupe_keys or key in seen:
            continue
        entry = _format_jsonl_candidate(project_name, key, bucket)
        if entry is None:
            continue
        raw_fp = (bucket["error_text"] or bucket["command_or_context"] or "")
        err_for_fp = re.sub(r"\s+", " ", raw_fp.strip())[:60].lower()
        if err_for_fp and err_for_fp in existing_keys:
            continue
        new_entries.append(entry)
        emitted_fps.add(key)
        new_from_jsonl += 1

    for c in brief_candidates:
        fp = c["candidate"][:60].lower()
        if fp in existing_keys or fp in seen:
            continue
        new_entries.append(_format_brief_candidate(c))
        emitted_fps.add(fp)
        new_from_briefs += 1

    # 4b. Build new doctrine candidate entries.
    new_doctrine_entries: list[str] = []
    new_doctrine_from_jsonl = 0
    new_doctrine_from_briefs = 0
    for key, (project_name, bucket) in all_doctrine_buckets.items():
        fp = (bucket["rule"] or "").strip().lower()[:80]
        if fp and (fp in existing_doctrine_keys or fp in seen):
            continue
        new_doctrine_entries.append(
            _format_doctrine_jsonl_candidate(project_name, bucket)
        )
        existing_doctrine_keys.add(fp)
        emitted_fps.add(fp)
        new_doctrine_from_jsonl += 1

    for c in brief_doctrine_catches:
        fp = c["rule"].strip().lower()[:80]
        if fp in existing_doctrine_keys or fp in seen:
            continue
        new_doctrine_entries.append(_format_doctrine_brief_candidate(c))
        existing_doctrine_keys.add(fp)
        emitted_fps.add(fp)
        new_doctrine_from_briefs += 1

    total_new = len(new_entries) + len(new_doctrine_entries)

    if args.dry_run:
        print(f"# DRY RUN — would append {total_new} entries")
        print(f"#   Skill: {new_from_jsonl} from JSONL, {new_from_briefs} from briefs")
        print(f"#   Doctrine: {new_doctrine_from_jsonl} from JSONL, {new_doctrine_from_briefs} from briefs")
        for e in new_entries:
            print(e)
        if new_doctrine_entries:
            print("\n# --- Doctrine candidates ---")
            for e in new_doctrine_entries:
                print(e)
        return 0

    if total_new == 0:
        print(f"harvest: 0 new candidates (lookback {args.lookback_days}d).")
        _touch_harvest_heartbeat()
        _update_queue_header(queue_text, existing_only=True)
        return 0

    # 5a. Append skill entries to skill section.
    if new_entries:
        skill_marker = "<!-- New entries appended by harvest.py — most recent at top -->"
        insertion = skill_marker + "".join(new_entries)
        if skill_marker in queue_text:
            queue_text = queue_text.replace(skill_marker, insertion, 1)
        else:
            queue_text = queue_text + "\n" + insertion

    # 5b. Append doctrine entries to doctrine section.
    if new_doctrine_entries:
        doctrine_marker = "<!-- New doctrine entries appended by harvest.py — most recent at top -->"
        insertion = doctrine_marker + "".join(new_doctrine_entries)
        if doctrine_marker in queue_text:
            queue_text = queue_text.replace(doctrine_marker, insertion, 1)
        else:
            # Doctrine section not yet present — append both heading + entries.
            queue_text = queue_text + (
                "\n\n## Doctrine Candidates\n\n"
                "> Each entry below is an architectural decision caught in code review.\n"
                "> Promote by editing the source doc (CLAUDE.md, DOCTRINE_REGISTRY.md, "
                "or the named architecture doc), then delete the entry here.\n"
                "> Reject: delete with one-line rationale.\n\n"
                + doctrine_marker
                + "".join(new_doctrine_entries)
            )

    # 6. Update header (Last harvested + Open candidates count).
    queue_text = _update_queue_header(queue_text, existing_only=False)

    QUEUE_PATH.write_text(queue_text)
    _touch_harvest_heartbeat()
    _append_seen(emitted_fps)  # so promoted/rejected entries never re-emit
    print(
        f"harvest: appended {total_new} candidate"
        f"{'s' if total_new != 1 else ''} "
        f"(skill: {new_from_jsonl} JSONL + {new_from_briefs} briefs; "
        f"doctrine: {new_doctrine_from_jsonl} JSONL + {new_doctrine_from_briefs} briefs)."
    )
    return 0


def _update_queue_header(queue_text: str, *, existing_only: bool) -> str:
    """Refresh `Last harvested:` and `Open candidates:` lines in the queue.
    When `existing_only`, the queue wasn't modified — we still touch
    timestamps so callers can see the harvest ran."""
    now = _now_iso()
    new_text = re.sub(
        r"^\*\*Last harvested\*\*:.*$",
        f"**Last harvested**: {now}",
        queue_text,
        count=1,
        flags=re.MULTILINE,
    )
    # Count `## YYYY-MM-DD — [` headings (the entry marker)
    n_entries = len(re.findall(r"^## \d{4}-\d{2}-\d{2} — \[", new_text, re.MULTILINE))
    new_text = re.sub(
        r"^\*\*Open candidates\*\*:.*$",
        f"**Open candidates**: {n_entries} (target ≤20)",
        new_text,
        count=1,
        flags=re.MULTILINE,
    )
    if existing_only:
        try:
            QUEUE_PATH.write_text(new_text)
        except Exception:
            pass
    return new_text


if __name__ == "__main__":
    sys.exit(main())
