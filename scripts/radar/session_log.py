"""Per-project JSONL session log — the structured replacement for the
SKILL_DEBT.md + SKILL_INJECT_LOG.md + grep-on-code-violations.log triad
(all archived 2026-05-26 under `~/repo_docs/skills/_archive/`).

One row per event. Rows are JSON Lines; the schema lives in the docstring of
`append_event()` below. Per the skill-lifecycle refactor plan, this is the
machine-readable surface that `harvest.py` reads weekly to populate
`~/repo_docs/skills/SKILL_QUEUE.md`.

Design rules:
- Per-project — log lives in `~/.claude/projects/<slug>/session-log.jsonl`
  derived from `os.getcwd()` at the time of the hook firing.
- Append-only — never rewrites or truncates from this module. Rotation /
  archival is `harvest.py`'s job.
- Dedupe by fingerprint — identical events within the last 50 rows are
  skipped so a recurring error fires once per "session-ish" window.
- Silent no-op on any failure — never block Claude Code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# How many recent rows to scan when checking for dedupe collisions. A larger
# window suppresses more repeats but costs more disk reads; 50 is a balance
# that gives session-like behavior for ordinary multi-error sessions while
# letting the same error re-fire if it's been quiet for a while.
DEDUPE_LOOKBACK = 50

# Hard cap on JSONL file size before harvest.py is expected to rotate; the
# hook itself doesn't rotate — it just refuses to write past this so a runaway
# hook can't fill the disk. 50MB is generous; ~10MB would be a year of normal
# usage at observed rates.
MAX_LOG_BYTES = 50 * 1024 * 1024


def _slugify_cwd(cwd: str) -> str:
    """Match Claude Code's project-dir slugification: replace / with -,
    replace _ with -, prefix with -. Best-effort — if the encoded path
    doesn't match an existing dir, we still create it and write."""
    s = cwd.replace("/", "-").replace("_", "-")
    if not s.startswith("-"):
        s = "-" + s
    return s


def project_log_path(cwd: str | None = None) -> Path:
    """Return the absolute path to this project's session-log.jsonl.

    Creates the parent dir if missing. The caller can `Path.exists()` to
    distinguish first-write from subsequent-write, but the dir creation
    is unconditional so first-write doesn't error."""
    cwd = cwd or os.getcwd()
    slug = _slugify_cwd(cwd)
    log_dir = Path.home() / ".claude" / "projects" / slug
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "session-log.jsonl"


def compute_dedupe_key(*parts: str) -> str:
    """Stable fingerprint over any joined strings — normalized whitespace +
    lowercase + sha256. Used to suppress duplicate events within a session."""
    text = " ".join(p for p in parts if p)
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(norm.encode("utf-8", errors="replace")).hexdigest()[:16]


def _recent_dedupe_keys(log_path: Path, n: int = DEDUPE_LOOKBACK) -> set[str]:
    """Read the last `n` rows of the JSONL and return their dedupe_keys.
    Returns an empty set on any failure (silent-no-op discipline)."""
    if not log_path.exists():
        return set()
    try:
        # tail-N read: cheap for moderate files, fine here at our scale
        with log_path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            # heuristic — average row is ~400 bytes; read ~4× n × 400 bytes
            chunk_size = min(size, max(4096, n * 1600))
            f.seek(size - chunk_size, os.SEEK_SET)
            tail = f.read().decode("utf-8", errors="replace")
        lines = tail.splitlines()[-n:]
        keys: set[str] = set()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if "dedupe_key" in row:
                    keys.add(row["dedupe_key"])
            except Exception:
                continue
        return keys
    except Exception:
        return set()


def append_event(
    event_type: str,
    *,
    tool: str = "",
    command_or_context: str = "",
    error_text: str = "",
    skill_match: dict | None = None,
    outcome: str = "",
    extra: dict[str, Any] | None = None,
    dedupe_parts: tuple[str, ...] | None = None,
) -> bool:
    """Append one event row to the per-project session-log.jsonl.

    Row schema:
        {
          "ts": ISO-8601 with timezone,
          "event_type": "bash_error" | "tool_denied" | "timeout"
                        | "parser_error" | "llm_unavailable" | "grep_on_code"
                        | "pivot" | "gotcha" | "brief_authored"
                        | "doctrine_match",
          "tool": tool name (best-effort),
          "command_or_context": truncated context (≤400 chars),
          "error_text": truncated error text (≤600 chars),
          "skill_match": {"score": float, "skill": str, "header": str} or null,
          "outcome": "matched" | "missed" | "injected" | "violation"
                     | "recorded" | "caught_in_review" | "violated_in_code"
                     | "",
          "dedupe_key": short sha256 fingerprint over the dedupe parts.
        }

    The default dedupe fingerprint is `(event_type, error_text or
    command_or_context)`. Callers may override via `dedupe_parts` — doctrine
    rows fingerprint over `(rule, outcome)` so the same rule firing twice for
    the same outcome within the dedupe window is a silent no-op.

    Extra fields are merged into the row verbatim. Returns True if the row was
    written, False if dedupe-suppressed or an error swallowed the write."""
    try:
        log_path = project_log_path()
        if log_path.exists() and log_path.stat().st_size > MAX_LOG_BYTES:
            return False  # refuse to write past the cap

        if dedupe_parts is not None:
            dedupe_key = compute_dedupe_key(*dedupe_parts)
        else:
            dedupe_key = compute_dedupe_key(event_type, error_text or command_or_context)
        if dedupe_key in _recent_dedupe_keys(log_path):
            return False

        row: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).astimezone().strftime(
                "%Y-%m-%dT%H:%M:%S%z"
            ),
            "event_type": event_type,
            "tool": tool,
            "command_or_context": command_or_context[:400],
            "error_text": error_text[:600],
            "skill_match": skill_match,
            "outcome": outcome,
            "dedupe_key": dedupe_key,
        }
        if extra:
            row.update(extra)

        with log_path.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False  # never block Claude Code
