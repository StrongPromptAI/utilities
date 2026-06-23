#!/usr/bin/env python3
"""build_brain.py — re-runnable build of the coach's value-prop + stakeholder corpus,
driven by the `audience` front-matter tag on THJ docs. Reuses the kb ingest pipeline.

No hand-maintained manifest. Every THJ project + stakeholder doc carries an `audience:`
tag (`rep-facing` | `mixed` | `internal`, authored repo_docs@353c7d3). This script
QUERIES that tag — include `rep-facing` + `mixed`, exclude `internal` — so the corpus
tracks the docs automatically. (TKR_GENERIC discipline: intent on the source, never
hand-edit the target.)

Pathways
--------
  INPUT   ~/repo_docs/thj/{project,stakeholders}/*.md   (override: COACH_BRAIN_SOURCE_ROOT)
          — root files only; subdirs (stakeholders/bawa/, …) are out of scope.
  FILTER  keep audience ∈ {rep-facing, mixed}; drop internal.
          FAIL-CLOSED: an in-scope .md with NO `audience` tag aborts the build.
  CORPUS  kb Postgres reference_docs + reference_doc_chunks  (category='thj_brain')
          — one row per source doc; `audience` stored as a row FACET. The front-matter
            block is parsed for `audience` and STRIPPED from the body before chunk +
            embed, else "audience: internal" becomes searchable prose (brief 26-6-22).
          — chunk + embed via kb_core (nomic 768d ONNX, in-process). Idempotent upsert.

NOT ingested here: STAKEHOLDER_VALUE_REGISTRY.md — the distilled value spine loaded as
the runtime FLOOR (COACH_BRAIN §4), not a retrieved corpus.

Run (from the utilities repo root, so the root `utilities` env + kb_core resolve):
  uv run python services/coach/build_brain.py --dry-run    # discover only, no DB/embed
  uv run python services/coach/build_brain.py              # discover + ingest
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# kb_core lives in <repo>/scripts — put it on the path so the ingest half resolves.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

SOURCE_ROOT = Path(os.environ.get("COACH_BRAIN_SOURCE_ROOT", "~/repo_docs/thj")).expanduser()
SCOPE_GLOBS = ("project/*.md", "stakeholders/*.md")   # root files only (no rglob)
AUDIENCE_INCLUDE = {"rep-facing", "mixed"}
AUDIENCE_VALID = {"rep-facing", "mixed", "internal"}
CATEGORY = "thj_brain"

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_AUDIENCE_KEY = re.compile(r"^audience:\s*(\S+)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Discover the include set by audience tag (fail-closed on untagged)
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (audience, body_without_frontmatter). audience is None if untagged."""
    m = _FRONTMATTER.match(text)
    if not m:
        return None, text
    aud = _AUDIENCE_KEY.search(m.group(1))
    return (aud.group(1) if aud else None), text[m.end():]


def discover(root: Path) -> list[dict]:
    """In-scope docs tagged rep-facing|mixed, front-matter stripped.

    Fail-closed: an in-scope .md with no/invalid `audience` aborts — a doc that skipped
    classification blocks the build, never silently leaks or drops.
    """
    docs: list[dict] = []
    for glob in SCOPE_GLOBS:
        for path in sorted(root.glob(glob)):
            audience, body = split_frontmatter(path.read_text(encoding="utf-8"))
            rel = str(path.relative_to(root))
            if audience is None:
                raise SystemExit(f"FAIL-CLOSED: {rel} has no `audience` front-matter tag")
            if audience not in AUDIENCE_VALID:
                raise SystemExit(f"FAIL-CLOSED: {rel} invalid audience '{audience}'")
            if audience in AUDIENCE_INCLUDE:
                docs.append({"rel": rel, "audience": audience, "body": body, "path": str(path)})
    return docs


# ---------------------------------------------------------------------------
# Ingest → reference_docs (+ audience facet) + reference_doc_chunks (kb_core)
# ---------------------------------------------------------------------------


def ensure_audience_column(cur) -> None:
    """Additive, idempotent: the `audience` facet on the shared reference_docs table."""
    cur.execute("ALTER TABLE reference_docs ADD COLUMN IF NOT EXISTS audience text")


def ingest_doc(conn, doc: dict, chunk_by_sections, get_embedding) -> int:
    """Upsert one source doc (keyed on title+category) + replace its chunks. Returns chunk count."""
    title, content, audience = doc["rel"], doc["body"], doc["audience"]
    chunks = chunk_by_sections(content, min_chunk_size=100)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM reference_docs WHERE title = %s AND category = %s", (title, CATEGORY))
        row = cur.fetchone()
        if row:
            doc_id = row["id"]
            cur.execute(
                "UPDATE reference_docs SET content = %s, source_file = %s, audience = %s WHERE id = %s",
                (content, doc["path"], audience, doc_id),
            )
        else:
            cur.execute(
                "INSERT INTO reference_docs (title, category, content, source_file, audience) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (title, CATEGORY, content, doc["path"], audience),
            )
            doc_id = cur.fetchone()["id"]
        cur.execute("DELETE FROM reference_doc_chunks WHERE doc_id = %s", (doc_id,))
        for idx, chunk_text in enumerate(chunks):
            cur.execute(
                "INSERT INTO reference_doc_chunks (doc_id, chunk_idx, text, embedding) VALUES (%s, %s, %s, %s)",
                (doc_id, idx, chunk_text, get_embedding(chunk_text)),
            )
    return len(chunks)


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the coach corpus from THJ docs tagged rep-facing|mixed.")
    ap.add_argument("--dry-run", action="store_true", help="discover only; no DB / embed")
    ap.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    args = ap.parse_args()

    if not args.source_root.is_dir():
        raise SystemExit(f"FAIL-CLOSED: source root not found: {args.source_root}")

    docs = discover(args.source_root)
    by = {a: sum(1 for d in docs if d["audience"] == a) for a in sorted(AUDIENCE_INCLUDE)}
    print(f"[build_brain] include set: {len(docs)} docs {by}")
    for d in docs:
        print(f"  {d['audience']:>10}  {d['rel']}")

    if args.dry_run:
        print("[build_brain] --dry-run: skipping ingest")
        return 0

    # Lazy imports: only the real run needs the DB URL (Railway pull) + ONNX embed.
    from kb_core import chunk_by_sections, get_db, get_embedding

    total_chunks = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            ensure_audience_column(cur)
        for d in docs:
            n = ingest_doc(conn, d, chunk_by_sections, get_embedding)
            total_chunks += n
            print(f"[build_brain] ingested {d['rel']} ({d['audience']}) — {n} chunks")
        conn.commit()
    print(f"[build_brain] done — {len(docs)} docs, {total_chunks} chunks → reference_docs (category='{CATEGORY}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
