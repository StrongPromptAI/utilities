"""Claude Code Stop hook — session-end brief author trigger.

Fires when Claude finishes responding at the end of a session. Sets a
`brief-pending.txt` marker in the project's session dir so the next
UserPromptSubmit can inject a brief-author notice. The actual brief authoring
happens in the NEXT session — at Stop time the agent is wrapping up, so we
defer the work to when there's a fresh prompt context.

Phase 2 addition (Doctrine Radar): when this session's JSONL has ≥1
doctrine_match row with outcome="caught_in_review", we ALSO write a sibling
marker `doctrine-catches-pending.txt` so the next UserPromptSubmit's brief
notice can include the doctrine prompt.

Design notes:
- Always fires (per HITL 2026-05-26: "always prompt, even on quiet sessions")
- Marker contains ISO timestamp; cleared when a brief file is authored after that ts
- Silent no-op on any failure; never blocks Claude Code

Path conventions:
- Marker: ~/.claude/projects/<cwd-slug>/brief-pending.txt
- Doctrine marker: ~/.claude/projects/<cwd-slug>/doctrine-catches-pending.txt
- Brief location: <cwd>/symlink_docs/briefs/  (resolves to ~/repo_docs/<project>/briefs/)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_log import _slugify_cwd, project_log_path  # noqa: E402

HEARTBEAT_PATH = Path.home() / ".claude" / "last-brief-trigger.txt"

# How far back to scan for doctrine catches when deciding whether to flag the
# sibling marker. Session-scoped — the goal is "this session had a catch",
# not "ever had a catch."
DOCTRINE_SCAN_WINDOW_HOURS = 24


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _project_state_dir(cwd: str | None = None) -> Path:
    cwd = cwd or os.getcwd()
    slug = _slugify_cwd(cwd)
    p = Path.home() / ".claude" / "projects" / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def _touch_heartbeat() -> None:
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(_now_iso() + "\n")
    except Exception:
        pass


def _has_doctrine_catches_this_session(cwd: str | None = None) -> int:
    """Count doctrine_match rows in the project log within the recent scan
    window. Returns 0 on any read/parse failure (silent no-op discipline)."""
    try:
        log_path = project_log_path(cwd)
        if not log_path.exists():
            return 0
        cutoff = datetime.now(timezone.utc).astimezone()
        from datetime import timedelta
        cutoff = cutoff - timedelta(hours=DOCTRINE_SCAN_WINDOW_HOURS)
        count = 0
        with log_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("event_type") != "doctrine_match":
                    continue
                # Only count human-judged catches; auto-fires are observability,
                # not promotion-track signal.
                if row.get("outcome") != "caught_in_review":
                    continue
                ts = row.get("ts", "")
                try:
                    row_dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
                except Exception:
                    continue
                if row_dt >= cutoff:
                    count += 1
        return count
    except Exception:
        return 0


def main() -> int:
    # Drain stdin; we don't actually need the payload, but Claude Code passes
    # event JSON on stdin for every hook invocation.
    try:
        sys.stdin.read()
    except Exception:
        pass

    try:
        state_dir = _project_state_dir()
        marker = state_dir / "brief-pending.txt"
        marker.write_text(_now_iso() + "\n")

        # Phase 2 — sibling marker for doctrine catches. The next
        # UserPromptSubmit's brief-pending notice will read it and add the
        # doctrine-prompt section if present.
        doctrine_count = _has_doctrine_catches_this_session()
        doctrine_marker = state_dir / "doctrine-catches-pending.txt"
        if doctrine_count > 0:
            doctrine_marker.write_text(f"{_now_iso()}\t{doctrine_count}\n")
        elif doctrine_marker.exists():
            # Clear stale marker — no catches this session.
            try:
                doctrine_marker.unlink()
            except Exception:
                pass

        _touch_heartbeat()
    except Exception:
        pass

    # Stop hook output is shown to the user. Keep it minimal — the real
    # notice fires in the next session via the UserPromptSubmit hook.
    return 0


if __name__ == "__main__":
    sys.exit(main())
