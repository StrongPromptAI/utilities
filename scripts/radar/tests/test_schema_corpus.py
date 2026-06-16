"""Tests for schema_corpus — the schema.sql COMMENT parser/renderer/registry
behind the Schema Radar corpus (plan thj/26-6-16).

Pins the parse contract (table + column extraction, ''-escaping, multi-line
bodies, the KIND-based schema.table vs table.column disambiguation, public-wins
on a sandbox dup), the substantive-comment filter, the chunk renderer, and the
repo_for_cwd resolution. Pure Python — no embed service, no DB.

Run: `uv run --project ~/repos/utilities python scripts/radar/tests/test_schema_corpus.py`
Exits 0 on full pass, 1 on any failure.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import schema_corpus as sc  # noqa: E402

FIXTURE = """
CREATE TABLE public.t1 (id integer);
COMMENT ON TABLE public.t1 IS 'Primary table with a sufficiently long comment to be substantive on its own.';
COMMENT ON COLUMN public.t1.id IS 'The primary key.';
COMMENT ON COLUMN public.t1.note IS 'A note that isn''t simple and
spans two physical lines.';
COMMENT ON TABLE sandbox.t1 IS 'Sandbox twin comment that should LOSE to public.';
COMMENT ON TABLE public.thin IS 'too short';
COMMENT ON TABLE public.colsonly IS 'hi';
COMMENT ON COLUMN public.colsonly.x IS 'Only this column comment keeps the table.';
"""


def _check(name: str, cond: bool) -> bool:
    print(f"{'PASS' if cond else 'FAIL'} — {name}")
    return cond


def _write_fixture() -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False)
    f.write(FIXTURE)
    f.close()
    return f.name


def main() -> int:
    results: list[bool] = []
    path = _write_fixture()
    parsed = sc.parse_comments(path)

    # ── _table_col disambiguation (KIND, not arity) ──
    results.append(_check("TABLE schema.table -> (table, None)",
                          sc._table_col("TABLE", "public.t1") == ("t1", None)))
    results.append(_check("COLUMN schema.table.col -> (table, col)",
                          sc._table_col("COLUMN", "public.t1.id") == ("t1", "id")))
    results.append(_check("TABLE bare table -> (table, None)",
                          sc._table_col("TABLE", "t1") == ("t1", None)))
    results.append(_check("COLUMN table.col (no schema) -> (table, col)",
                          sc._table_col("COLUMN", "t1.id") == ("t1", "id")))

    # ── parse extraction ──
    results.append(_check("table comment extracted",
                          parsed["t1"]["table"].startswith("Primary table")))
    results.append(_check("public wins over sandbox dup",
                          "LOSE" not in parsed["t1"]["table"]))
    results.append(_check("column comment extracted",
                          parsed["t1"]["columns"].get("id") == "The primary key."))
    results.append(_check("'' -escape unescaped to single quote",
                          "isn't simple" in parsed["t1"]["columns"].get("note", "")))
    results.append(_check("multi-line comment body captured",
                          "spans two physical lines" in parsed["t1"]["columns"].get("note", "")))

    # ── substantive filter ──
    results.append(_check("thin table (short, no cols) NOT substantive",
                          sc.is_substantive(parsed["thin"]) is False))
    results.append(_check("short table comment + a column IS substantive",
                          sc.is_substantive(parsed["colsonly"]) is True))
    results.append(_check("long table comment IS substantive",
                          sc.is_substantive(parsed["t1"]) is True))

    # ── render + build_chunks ──
    chunk = sc.render_chunk("t1", parsed["t1"])
    results.append(_check("render_chunk leads with 'table: comment'",
                          chunk.startswith("t1: Primary table")))
    results.append(_check("render_chunk lists columns with bullet",
                          "\n· id: The primary key." in chunk))
    tables = [c["table"] for c in sc.build_chunks(path)]
    results.append(_check("build_chunks excludes thin, includes substantive, sorted",
                          tables == ["colsonly", "t1"]))

    # ── lookup ──
    results.append(_check("lookup substantive table -> chunk", bool(sc.lookup(path, "t1"))))
    results.append(_check("lookup thin table -> None", sc.lookup(path, "thin") is None))
    results.append(_check("lookup missing table -> None", sc.lookup(path, "nope") is None))

    # ── repo_for_cwd (temp registry, prefix match) ──
    reg = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    reg.write(json.dumps({"demo": {"repo_root": "/tmp/demo", "schema_sql": "/tmp/demo/s.sql"}}))
    reg.close()
    orig = sc.REPOS_REGISTRY
    try:
        sc.REPOS_REGISTRY = Path(reg.name)
        slug_in, _ = sc.repo_for_cwd("/tmp/demo/backend/db")
        slug_out, _ = sc.repo_for_cwd("/tmp/other")
        slug_exact, _ = sc.repo_for_cwd("/tmp/demo")
        slug_prefixfalse, _ = sc.repo_for_cwd("/tmp/demonstration")  # not a path-boundary match
        results.append(_check("repo_for_cwd matches subdir", slug_in == "demo"))
        results.append(_check("repo_for_cwd matches exact root", slug_exact == "demo"))
        results.append(_check("repo_for_cwd rejects unrelated cwd", slug_out is None))
        results.append(_check("repo_for_cwd rejects non-boundary prefix", slug_prefixfalse is None))
    finally:
        sc.REPOS_REGISTRY = orig

    Path(path).unlink(missing_ok=True)
    Path(reg.name).unlink(missing_ok=True)

    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
