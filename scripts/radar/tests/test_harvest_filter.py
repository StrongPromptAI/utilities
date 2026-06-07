"""Tests for the radar_harvest bash-error noise filter.

The harvest emitted a skill candidate for EVERY logged bash_error, so the queue
filled with exploratory misses (`ls: No such file`, one-off psql/python errors)
— a 267-entry backlog by 2026-06-07. `_is_nonskill_bash_noise` is the fix:
filter file/command "misses" outright, and defer one-off errors that match no
skill domain until they recur. `_prune_queue` applies the same predicate to
clean the existing queue.

Run: `uv run --project ~/repos/utilities python scripts/radar/tests/test_harvest_filter.py`
Exits 0 on full pass, 1 on any failure. Pure Python — no embed service.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import radar_harvest as h  # noqa: E402


def _check(name: str, cond: bool) -> bool:
    print(f"{'PASS' if cond else 'FAIL'} — {name}")
    return cond


def _bucket(**kw) -> dict:
    base = {
        "event_types": {"bash_error"},
        "outcomes": set(),
        "error_text": "",
        "command_or_context": "",
        "skill_match": None,
        "count": 1,
        "last_seen": "2026-06-07T00:00:00-0600",
    }
    base.update(kw)
    return base


def main() -> int:
    ok = True

    # (1) MISS patterns are noise regardless of count (even a recurring one).
    ok &= _check("ls not-found is noise",
                 h._is_nonskill_bash_noise("ls: dr_bawa: No such file or directory", 5))
    ok &= _check("command not found is noise",
                 h._is_nonskill_bash_noise("foo: command not found", 3))
    ok &= _check("zsh cd miss is noise",
                 h._is_nonskill_bash_noise("(eval):cd:1: no such file or directory: x", 1))

    # (2) Recurrence floor: a one-off error is noise (score is NOT consulted —
    #     it's empirically always 0.5-0.9 and can't discriminate)...
    ok &= _check("one-off non-miss is noise",
                 h._is_nonskill_bash_noise("ERROR: column x does not exist", 1))
    # ...but recurrence rescues it (deferred, not suppressed).
    ok &= _check("recurring error kept",
                 not h._is_nonskill_bash_noise("ERROR: column x does not exist", 2))

    # (3) _format_jsonl_candidate drops noise buckets, keeps real recurring ones.
    ok &= _check("_format drops noise bucket",
                 h._format_jsonl_candidate("thj", "k",
                     _bucket(error_text="ls: x: No such file or directory")) is None)
    ok &= _check("_format keeps recurring real bucket",
                 h._format_jsonl_candidate("thj", "k", _bucket(
                     error_text="asyncpg relation does not exist",
                     skill_match={"skill": "postgres", "score": 0.66}, count=3)) is not None)

    # (4) _prune_queue drops noise blocks, keeps brief candidates + doctrine.
    sample = (
        "# Skill Queue\n\n**Last harvested**: x\n**Open candidates**: 2 (target ≤20)\n\n"
        "## Skill Candidates\n\n"
        "<!-- New entries appended by harvest.py — most recent at top -->\n"
        "## 2026-06-01 — [thj] ls miss\n\n**Category**: gotcha\n"
        "**Target skill**: NEW skill OR existing — needs disambiguation\n"
        "**Evidence**: session-log.jsonl `thj` — 1 hit, last seen x\n"
        "**One-liner**: ls: x: No such file or directory\n**Dedupe key**: `a`\n\n---\n"
        "## 2026-06-01 — [from b.md] real candidate\n\n**Category**: anti-pattern\n"
        "**Target skill**: foo\n**Evidence**: brief `b.md` — ev\n"
        "**One-liner**: a real authored candidate\n\n---\n"
        "\n## Doctrine Candidates\n\n(unchanged)\n"
    )
    pruned, kept, dropped = h._prune_queue(sample)
    ok &= _check("prune drops exactly 1 noise block", dropped == 1)
    ok &= _check("prune keeps 1 (the brief candidate)", kept == 1)
    ok &= _check("prune keeps the brief candidate text",
                 "a real authored candidate" in pruned and "[from b.md]" in pruned)
    ok &= _check("prune removes the ls-miss block",
                 "ls: x: No such file" not in pruned)
    ok &= _check("prune preserves doctrine section untouched",
                 "## Doctrine Candidates" in pruned and "(unchanged)" in pruned)

    print("ALL PASS" if ok else "SOME FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
