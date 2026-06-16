"""Tests for the shared radar provenance block (plan thj/26-6-16 Phase 1).

Pins the `<radar source= trust=>` contract that every corpus renders through:
the exact tag shape, attribute escaping, the no-`score=`-leak guarantee, and
that each retrofitted renderer (schema, doctrine, skill) emits a well-formed
block. Pure Python — no embed service, no DB.

Run: `uv run --project ~/repos/utilities python scripts/radar/tests/test_provenance_block.py`
Exits 0 on full pass, 1 on any failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from output_adapter import render_radar_block  # noqa: E402
from doctrine_registry import render_doctrine_section  # noqa: E402
import radar_prompt as rp  # noqa: E402


def _check(name: str, cond: bool) -> bool:
    print(f"{'PASS' if cond else 'FAIL'} — {name}")
    return cond


def _well_formed(block: str) -> bool:
    """A radar block opens with `<radar source=".." trust="..">`, closes with
    `</radar>`, and carries no `score=` attribute."""
    return (
        block.startswith("<radar source=\"")
        and " trust=\"" in block.split("\n", 1)[0]
        and block.split("\n", 1)[0].rstrip().endswith(">")
        and block.rstrip().endswith("</radar>")
        and "score=" not in block
    )


def main() -> int:
    results: list[bool] = []

    # ── render_radar_block shape (PY-1 contract) ──
    b = render_radar_block(
        "body text", source="schema:thj/schema.sql", trust="cached:verify-vs-live"
    )
    results.append(_check(
        "opening tag is the exact source+trust literal",
        b.startswith('<radar source="schema:thj/schema.sql" trust="cached:verify-vs-live">'),
    ))
    results.append(_check("no score= attribute leaks", "score=" not in b))
    results.append(_check("explicit close tag", b.rstrip().endswith("</radar>")))
    results.append(_check("body is carried verbatim", "body text" in b))
    results.append(_check("body on its own line between tags",
                          b == '<radar source="schema:thj/schema.sql" '
                               'trust="cached:verify-vs-live">\nbody text\n</radar>'))

    # ── attribute escaping keeps the opening tag well-formed ──
    esc = render_radar_block(
        "x", source='doctrine:A & B "quote" <tag>', trust="learned:judge-applicability"
    )
    results.append(_check("source & escaped", "&amp;" in esc))
    results.append(_check("source \" escaped", "&quot;" in esc))
    results.append(_check("source < > escaped", "&lt;tag&gt;" in esc))
    results.append(_check("escaped block still well-formed", _well_formed(esc)))

    # ── body stays raw (not XML-escaped) — it's LLM display context ──
    raw = render_radar_block(
        "see `\\d+` & <table> here", source="schema:r/schema.sql", trust="cached:verify-vs-live"
    )
    results.append(_check("body NOT escaped (raw prose preserved)",
                          "& <table>" in raw and "&amp;" not in raw))

    # ── schema renderer emits a well-formed block ──
    schema_block = rp.render_schema_section(
        "thj",
        {"table": "faithfulness_verdict", "text": "Public-only telemetry …", "score": 0.81},
    )
    results.append(_check("schema block well-formed", _well_formed(schema_block)))
    results.append(_check("schema source prefix + path",
                          schema_block.startswith('<radar source="schema:thj/schema.sql"')))
    results.append(_check("schema trust = cached:verify-vs-live",
                          'trust="cached:verify-vs-live"' in schema_block))
    results.append(_check("schema score (0.81) not rendered", "0.81" not in schema_block))
    results.append(_check("schema body carries table + comment",
                          "faithfulness_verdict" in schema_block and "Public-only" in schema_block))

    # ── doctrine renderer emits a well-formed block ──
    doctrine_block = render_doctrine_section(
        {"title": "No keyword matching of patient text", "source": "CLAUDE.md §1",
         "receipt": "2026-05-08 — first line\nsecond line", "score": 0.82},
    )
    results.append(_check("doctrine block well-formed", _well_formed(doctrine_block)))
    results.append(_check("doctrine source = doctrine:<title>",
                          'source="doctrine:No keyword matching of patient text"' in doctrine_block))
    results.append(_check("doctrine trust = learned:judge-applicability",
                          'trust="learned:judge-applicability"' in doctrine_block))
    results.append(_check("doctrine score (0.82) not rendered", "0.82" not in doctrine_block))
    results.append(_check("doctrine body carries rule + source + first receipt line",
                          "Rule:" in doctrine_block and "CLAUDE.md §1" in doctrine_block
                          and "first line" in doctrine_block and "second line" not in doctrine_block))

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
