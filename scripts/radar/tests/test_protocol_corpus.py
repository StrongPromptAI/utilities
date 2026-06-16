"""Tests for protocol_corpus — the protocol_component → chunk transform behind
the Protocol Radar corpus (plan thj/26-6-16 Phase 2).

Pins the section-granularity extractor: a content chunk (title + goal +
talking_points), one governance chunk per substantive clinical_patterns sub-key,
talking_points NOT double-counted as governance, thin/structural values dropped,
the avoid-class governance key KEPT (the index-all-units rule). Pure Python.

Run: `uv run --project ~/repos/utilities python scripts/radar/tests/test_protocol_corpus.py`
Exits 0 on full pass, 1 on any failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import protocol_corpus as pc  # noqa: E402


def _check(name: str, cond: bool) -> bool:
    print(f"{'PASS' if cond else 'FAIL'} — {name}")
    return cond


ROWS = [
    {
        "component_key": "knee_beartooth_trout_34",
        "protocol_id": "TKR_PRIMARY",
        "title": "Breathing exercises",
        "content_goal": "Teach deep breathing to prevent post-op pneumonia.",
        "clinical_patterns": {
            "talking_points": [
                "Take deep breaths every 1-2 hours for the first 24-48 hours",
                "Deep breathing expands the lungs and helps prevent pneumonia",
            ],
            "severity": "high",  # thin/structural — dropped
        },
    },
    {
        "component_key": "knee_coaching_style",
        "protocol_id": "TKR_PRIMARY",
        "title": "Coaching style",
        "content_goal": None,
        "clinical_patterns": {
            "avoid": [
                "Clinical status verdicts in Eva's own voice",
                "Numeric pain scales that force false precision",
            ],
            "language_rules": ["Use categorical trends, never 1-10 scores"],
            "min_weeks": 6,  # thin/structural — dropped
        },
    },
    {  # JSON-string clinical_patterns + missing component_key edge cases
        "component_key": "",
        "protocol_id": "TKR_PRIMARY",
        "title": "orphan",
        "content_goal": "x",
        "clinical_patterns": "{}",
    },
]


def main() -> int:
    results: list[bool] = []
    chunks = pc.build_chunks_from_rows(ROWS)
    by_key_section = {(c["component_key"], c["section"]) for c in chunks}

    # ── content chunk ──
    content = next((c for c in chunks if c["component_key"] == "knee_beartooth_trout_34"
                    and c["section"] is None), None)
    results.append(_check("content chunk emitted (section=None)", content is not None))
    results.append(_check("content chunk carries title + goal + talking points",
                          content is not None and "Breathing exercises" in content["text"]
                          and "Goal:" in content["text"] and "prevent pneumonia" in content["text"]))

    # ── governance chunks (per substantive clinical_patterns sub-key) ──
    results.append(_check("avoid governance chunk KEPT (index-all, not dropped)",
                          ("knee_coaching_style", "avoid") in by_key_section))
    results.append(_check("language_rules governance chunk kept",
                          ("knee_coaching_style", "language_rules") in by_key_section))
    avoid = next(c for c in chunks if c["section"] == "avoid")
    results.append(_check("governance chunk text labels the section",
                          "§ avoid" in avoid["text"] and "verdicts in Eva" in avoid["text"]))

    # ── talking_points NOT re-emitted as a governance section ──
    results.append(_check("talking_points not a governance section",
                          ("knee_beartooth_trout_34", "talking_points") not in by_key_section))

    # ── thin/structural values dropped ──
    results.append(_check("thin 'severity' dropped", ("knee_beartooth_trout_34", "severity") not in by_key_section))
    results.append(_check("thin 'min_weeks' dropped", ("knee_coaching_style", "min_weeks") not in by_key_section))

    # ── coaching_style has no content chunk (no title-goal-tp content) ──
    results.append(_check("coaching_style has no content chunk (governance only)",
                          ("knee_coaching_style", None) not in by_key_section))

    # ── empty component_key skipped ──
    results.append(_check("empty component_key row skipped",
                          not any(c["component_key"] == "" for c in chunks)))

    # ── deterministic sort ──
    keys = [(c["component_key"], c["section"] or "") for c in chunks]
    results.append(_check("chunks sorted by (component_key, section)", keys == sorted(keys)))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
