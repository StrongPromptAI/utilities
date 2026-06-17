"""
radar_ratio_report.py — the CONSUMER for the per-turn `radar_turn_aggregate`
feeder (plan thj/26-6-16 radar-prompt-amplifier-spry, Phase 0).

The prompt-submit hook (`radar_prompt.log_turn_aggregate`) emits one structured
row per turn into the per-project `session-log.jsonl`. A feeder that nothing
reads is a half-built loop (loop-design doctrine), so this is the read end: it
turns those rows into the prompt-amplifier picture — the enhancement ratio
(how much radar buttressed how little typing), the substance-vs-nag byte split,
the collision rate (how often multiple corpora fire together), and per-surface
fire frequency. That picture is what informs the Phase 1 (nag demotion) and
Phase 2 (coverage) decisions — so the signal reaches a decision instead of
evaporating.

JSONL, not Postgres, on purpose: the radar is a global, dependency-light hook
that runs headless in every repo and must never block Claude Code on a DB. The
session-log IS the radar's structured feeder store (its docstring: "the
machine-readable surface harvest.py reads weekly"). This reader is the analysis
surface over it.

Run (current project):  uv run --project ~/repos/utilities python \
                          scripts/radar/radar_ratio_report.py
     (a specific log):   ... radar_ratio_report.py --log ~/.claude/projects/<slug>/session-log.jsonl
     (all projects):     ... radar_ratio_report.py --all
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from session_log import project_log_path  # reuse the same path resolver


def _pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.0f}%" if d else "—"


def _quantile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, int(q * (len(s) - 1) + 0.5))
    return s[i]


def _load_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("event_type") == "radar_turn_aggregate":
                rows.append(r)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Radar prompt-amplifier report (reads radar_turn_aggregate rows).")
    ap.add_argument("--log", help="explicit session-log.jsonl path")
    ap.add_argument("--all", action="store_true", help="scan every ~/.claude/projects/*/session-log.jsonl")
    ap.add_argument("--last", type=int, default=0, help="only the most recent N turns (e.g. scope to one bug-hunt loop)")
    ap.add_argument("--json", action="store_true", help="emit the summary as JSON")
    args = ap.parse_args()

    if args.all:
        paths = [Path(p) for p in glob.glob(str(Path.home() / ".claude" / "projects" / "*" / "session-log.jsonl"))]
    elif args.log:
        paths = [Path(args.log)]
    else:
        paths = [project_log_path(os.getcwd())]

    rows = _load_rows(paths)
    if not rows:
        print(f"No radar_turn_aggregate rows in: {', '.join(str(p) for p in paths)}")
        print("(Fire a few prompts with the instrumented hook first.)")
        return 0

    if args.last and len(rows) > args.last:
        rows = rows[-args.last:]  # scope to the most recent N turns (one loop's worth)

    n = len(rows)
    ratios = [float(r.get("ratio", 0)) for r in rows]
    radar_bytes = [int(r.get("radar_bytes", 0)) for r in rows]
    sub_bytes = sum(i.get("bytes", 0) for r in rows for i in r.get("injections", []) if i.get("kind") == "substance")
    nag_bytes = sum(i.get("bytes", 0) for r in rows for i in r.get("injections", []) if i.get("kind") == "nag")
    total_bytes = sub_bytes + nag_bytes or 1

    # collision = distinct substance corpora firing on a turn
    coll = [int(r.get("n_collision", 0)) for r in rows]
    coll_0 = sum(1 for c in coll if c == 0)
    coll_1 = sum(1 for c in coll if c == 1)
    coll_2 = sum(1 for c in coll if c == 2)
    coll_3p = sum(1 for c in coll if c >= 3)

    # per-surface fire frequency
    surfaces: dict[str, int] = {}
    for r in rows:
        for s in set(r.get("surfaces", [])):
            surfaces[s] = surfaces.get(s, 0) + 1

    # corpus coverage — two CATEGORIES with different silence semantics:
    #
    #   TOPIC corpora (schema / protocol / skill) are ambient context: they fire
    #   on semantic proximity to a table / component / skill doc. If the loop
    #   clearly TOUCHED that domain and the corpus stayed silent, that IS the
    #   mis-thresholded/broken signal — silence-while-touched = a real fault.
    #
    #   DOCTRINE is NOT a topic corpus — it is a violation-restatement ALARM. It
    #   matches the PROMPT against DOCTRINE_REGISTRY.md *rules* at the 0.78 bar
    #   (above the topic bars) and fires only when a prompt near-verbatim
    #   restates a rule's forbidden-action scenario (e.g. the 2026-05-26 fire:
    #   "skip classify_clinical_concern when the prior turn already classified"
    #   → "Safety inspector runs unconditionally", score 0.808). Editing or
    #   reasoning ABOUT doctrine docs (AUTH_LANES_DEF.md, CONVERSATION_DESIGN.md)
    #   does NOT trip it — those docs are skill-radar-indexed and surface as
    #   `skill:` fires. So doctrine=0% is the NORMAL, healthy state (no proposed
    #   action tripped a known rule); flagging it as broken is a false alarm, and
    #   lowering its bar to force fires only injects the WRONG rule as a
    #   high-stakes warning. Reported below as an alarm count, never as a
    #   not-firing fault. (Diagnosis: thj session 2026-06-17, repo_docs e89f01f.)
    TOPIC_CORPORA = {"schema", "protocol", "skill"}
    fired = set(surfaces.keys())
    corpora_not_firing = sorted(TOPIC_CORPORA - fired)
    doctrine_alarms = surfaces.get("doctrine", 0)

    nag_turns = sum(1 for r in rows if r.get("n_nag", 0) > 0)
    nag_only = sum(1 for r in rows if r.get("n_nag", 0) > 0 and r.get("n_substance", 0) == 0)

    summary = {
        "turns": n,
        "ratio_p50": round(_quantile(ratios, 0.5), 1),
        "ratio_p90": round(_quantile(ratios, 0.9), 1),
        "ratio_max": round(max(ratios), 1),
        "radar_bytes_p50": int(_quantile([float(b) for b in radar_bytes], 0.5)),
        "radar_bytes_p90": int(_quantile([float(b) for b in radar_bytes], 0.9)),
        "substance_byte_share": round(100.0 * sub_bytes / total_bytes, 1),
        "nag_byte_share": round(100.0 * nag_bytes / total_bytes, 1),
        "collision": {"0": coll_0, "1": coll_1, "2": coll_2, "3+": coll_3p},
        "surface_fire_pct": {s: round(100.0 * c / n, 1) for s, c in sorted(surfaces.items(), key=lambda x: -x[1])},
        "nag_turns": nag_turns,
        "nag_only_turns": nag_only,
        "topic_corpora_not_firing": corpora_not_firing,
        "doctrine_alarms": doctrine_alarms,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print(f"\nRadar prompt-amplifier report — {n} turns  ({', '.join(str(p) for p in paths)})\n")
    print("Enhancement ratio (radar_bytes / typed_bytes)   [90/10 target ≈ 9×]")
    print(f"  p50 {summary['ratio_p50']}×   p90 {summary['ratio_p90']}×   max {summary['ratio_max']}×")
    print("\nVolume per turn (radar bytes)")
    print(f"  p50 {summary['radar_bytes_p50']}   p90 {summary['radar_bytes_p90']}")
    print(f"  byte share — substance {summary['substance_byte_share']}%   nag {summary['nag_byte_share']}%")
    print("\nCollision (distinct substance corpora firing together)")
    print(f"  0: {coll_0}   1: {coll_1}   2: {coll_2}   3+: {coll_3p}   ({_pct(coll_2 + coll_3p, n)} of turns ≥2)")
    print("\nSurface fire frequency")
    for s, pct in summary["surface_fire_pct"].items():
        print(f"  {s:10s} {pct}%")
    print("\nTopic-corpus coverage (ambient context: schema / protocol / skill)")
    if corpora_not_firing:
        print(f"  ⚠ NOT firing in this window: {corpora_not_firing}")
        print(f"    → if the loop touched that domain, the corpus is mis-thresholded or broken; else expected.")
    else:
        print(f"  ✓ all three topic corpora fired at least once")
    print("\nDoctrine alarms (violation-restatement detector — silence is healthy)")
    if doctrine_alarms:
        print(f"  ⚠ {doctrine_alarms} turn(s) restated a known doctrine rule — review the fire(s).")
    else:
        print(f"  ✓ 0 — no prompt tripped a DOCTRINE_REGISTRY rule (the normal state; NOT a coverage gap)")
    print("\nNag health")
    print(f"  turns with a nag:   {nag_turns} ({_pct(nag_turns, n)})")
    print(f"  NAG-ONLY turns:     {nag_only} ({_pct(nag_only, n)})  ← wasted budget (no substance)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
