"""record-session-event — append a `pivot`, `gotcha`, or `doctrine` row to the
project's session-log.jsonl mid-session, so the weekly harvest can pick it up.

Designed to be called by Claude when a learning trigger fires during a session
(see `~/.claude/CLAUDE.md` § "Skill Harvest Triggers" and § "Doctrine Radar").
The hook can't observe these events automatically — they're meta-events about
the conversation, not errors — so this CLI is the entry point.

Usage:
    # Skill-track events (existing):
    uv run --project ~/repos/utilities python ~/repos/utilities/scripts/radar/radar_record.py \\
        --type pivot --note "Switched from approach X to Y after Z failed"

    uv run --project ~/repos/utilities python ~/repos/utilities/scripts/radar/radar_record.py \\
        --type gotcha --note "MacWhisper Format: JSON produces array of {speaker,text}"

    # Doctrine-track events (Phase 1):
    uv run --project ~/repos/utilities python ~/repos/utilities/scripts/radar/radar_record.py \\
        --type doctrine \\
        --rule "stage manager owns session observability" \\
        --source "CONVERSATION_BACKPLANE.md § Stage Manager" \\
        --outcome caught_in_review \\
        --touchpoint conversational.py --touchpoint check_in.py \\
        --evidence "PR #1234 — moved scattered observability writes into stage manager" \\
        --receipt "2026-05-26 probe_turn bug — observability scattered across handlers"

Doctrine dedupe: rows fingerprint over (rule, outcome). The same rule firing
twice for the same outcome within the JSONL dedupe window is a silent no-op,
so reviewers can re-record without duplicating signal.

Exit codes:
    0 — row written (or dedupe-suppressed; both are silent success)
    1 — invalid arguments
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_log import append_event  # noqa: E402

HEARTBEAT_PATH = Path.home() / ".claude" / "last-jsonl-write.txt"

VALID_TYPES = {"pivot", "gotcha", "doctrine"}
VALID_DOCTRINE_OUTCOMES = {"caught_in_review", "violated_in_code"}


def _touch_heartbeat() -> None:
    """Phase 4.5 — stamp last-jsonl-write.txt on successful write."""
    try:
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(
            datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
            + "\n"
        )
    except Exception:
        pass


def _record_skill_event(args: argparse.Namespace) -> int:
    """Existing path: pivot / gotcha rows. Uses default dedupe over (event_type,
    error_text)."""
    note = (args.note or "").strip()
    if not note:
        print("error: --note cannot be empty for pivot/gotcha", file=sys.stderr)
        return 1

    written = append_event(
        event_type=args.type,
        tool="Claude",
        command_or_context=(args.context or "")[:400],
        error_text=note[:600],
        outcome="recorded",
    )
    if written:
        _touch_heartbeat()
        print(f"recorded: {args.type}")
    else:
        print(f"deduped or suppressed: {args.type}")
    return 0


def _record_doctrine_event(args: argparse.Namespace) -> int:
    """Phase 1 path: doctrine_match rows. Dedupe over (rule, outcome) so the
    same rule firing on the same outcome within the dedupe window is a silent
    no-op. Extra fields (rule, rule_source, touchpoint, match_type, evidence,
    receipt) are written verbatim into the JSONL row."""
    rule = (args.rule or "").strip()
    outcome = (args.outcome or "").strip()
    if not rule:
        print("error: --rule is required for doctrine events", file=sys.stderr)
        return 1
    if outcome not in VALID_DOCTRINE_OUTCOMES:
        print(
            f"error: --outcome must be one of {sorted(VALID_DOCTRINE_OUTCOMES)} "
            f"for doctrine events (got {outcome!r})",
            file=sys.stderr,
        )
        return 1

    touchpoints = [t.strip() for t in (args.touchpoint or []) if t and t.strip()]
    extra: dict = {
        "rule": rule[:600],
        "rule_source": (args.source or "").strip()[:400],
        "touchpoint": touchpoints,
        "match_type": "manual",
        "evidence": (args.evidence or "").strip()[:600],
        "receipt": (args.receipt or "").strip()[:600],
    }

    written = append_event(
        event_type="doctrine_match",
        tool="Claude",
        command_or_context=(args.context or "")[:400],
        outcome=outcome,
        extra=extra,
        dedupe_parts=(rule, outcome),
    )
    if written:
        _touch_heartbeat()
        print(f"recorded: doctrine_match ({outcome})")
    else:
        print(f"deduped or suppressed: doctrine_match")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="record-session-event",
        description="Record a pivot, gotcha, or doctrine match to the project session-log.jsonl",
    )
    p.add_argument(
        "--type", required=True, choices=sorted(VALID_TYPES),
        help="Event class: pivot, gotcha, or doctrine.",
    )
    # Skill-track args
    p.add_argument(
        "--note", default=None,
        help="One-line description (required for pivot/gotcha; ≤600 chars stored).",
    )
    p.add_argument(
        "--context", default="",
        help="Optional context — file path, command, scenario name.",
    )
    # Doctrine-track args
    p.add_argument(
        "--rule", default=None,
        help="(doctrine only, required) The architectural rule that fired. "
             "Should match a `## Rule:` heading in DOCTRINE_REGISTRY.md.",
    )
    p.add_argument(
        "--source", default=None,
        help="(doctrine only) Doctrine doc reference, e.g. "
             "'CONVERSATION_BACKPLANE.md § Stage Manager'.",
    )
    p.add_argument(
        "--outcome", default=None,
        help="(doctrine only, required) One of: "
             f"{sorted(VALID_DOCTRINE_OUTCOMES)}.",
    )
    p.add_argument(
        "--touchpoint", action="append", default=[],
        help="(doctrine only) Touchpoint file/function affected. Repeatable.",
    )
    p.add_argument(
        "--evidence", default=None,
        help="(doctrine only) Code location, PR number, brief entry, etc.",
    )
    p.add_argument(
        "--receipt", default=None,
        help="(doctrine only) The incident/bug the rule exists to prevent.",
    )
    args = p.parse_args()

    if args.type == "doctrine":
        return _record_doctrine_event(args)
    return _record_skill_event(args)


if __name__ == "__main__":
    sys.exit(main())
