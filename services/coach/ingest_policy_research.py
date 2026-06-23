#!/usr/bin/env python3
"""ingest_policy_research.py — load the deep policy/legal research PDFs into the coach corpus.

These are dense, citation-heavy research docs (bundling risk, the 90-day surgical global
period, California employed-physician side-practice / concierge rules). They are the rep's
CREDIBILITY BACKSTOP for a sophisticated follow-up — NOT pitch material. So they ingest into
their OWN category `policy_research`, reachable ONLY via the `search_deep_research` tool
(agent.py), which the model calls only when a rep explicitly digs into the rules/specifics.
Keeping them out of `thj_brain` is the gate: a normal pitch question never pulls them.

Pipeline: PDF text (pypdf) → size-based chunks (kb_core.chunk_text) → nomic-768 embed
(kb_core.get_embedding) → upsert reference_docs + reference_doc_chunks (category='policy_research').
Idempotent: re-run cleanly replaces each doc's chunks (keyed on title+category).

Run from the utilities repo root:
  uv run --with pypdf python services/coach/ingest_policy_research.py
  uv run --with pypdf python services/coach/ingest_policy_research.py --dry-run   # extract + chunk counts only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# kb_core lives in <repo>/scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

CATEGORY = "policy_research"
CHUNK_SIZE = 1000   # dense policy paragraphs — keep more context per chunk than the default 512
CHUNK_OVERLAP = 120

THJ_RESEARCH = Path("~/repos/thj/symlink_docs/stakeholders").expanduser()

# (title, relative path under THJ_RESEARCH). Titles are how the coach will name the source.
DOCS = [
    ("California DME Bundling Risk (research)",
     "dme-research/California_DME_Bundling_Supplement.pdf"),
    ("90-Day Surgical Global Period — Surgeon Accountability (research)",
     "surgeon-research/90_day_global_period_validation.pdf"),
    ("California Employed-Physician Side-Practice & Concierge Rules (research)",
     "surgeon-research/California_Employed_Physicians_Side_Practice_Research.pdf"),
    ("TKA Prehab — Clinical Evidence Review (research)",
     "surgeon-research/TKA_Prehab_Evidence_Review.pdf"),
]


def extract_pdf(path: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    parts = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(p for p in parts if p)


def ingest_doc(conn, title: str, path: Path, content: str, chunk_text, get_embedding) -> int:
    """Upsert one PDF (keyed on title+category) + replace its chunks. Returns chunk count."""
    chunks = chunk_text(content, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM reference_docs WHERE title = %s AND category = %s", (title, CATEGORY))
        row = cur.fetchone()
        if row:
            doc_id = row["id"]
            cur.execute(
                "UPDATE reference_docs SET content = %s, source_file = %s WHERE id = %s",
                (content, str(path), doc_id),
            )
        else:
            cur.execute(
                "INSERT INTO reference_docs (title, category, content, source_file) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (title, CATEGORY, content, str(path)),
            )
            doc_id = cur.fetchone()["id"]
        cur.execute("DELETE FROM reference_doc_chunks WHERE doc_id = %s", (doc_id,))
        for idx, chunk in enumerate(chunks):
            cur.execute(
                "INSERT INTO reference_doc_chunks (doc_id, chunk_idx, text, embedding) VALUES (%s, %s, %s, %s)",
                (doc_id, idx, chunk, get_embedding(chunk)),
            )
    return len(chunks)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest the deep policy-research PDFs into the coach corpus.")
    ap.add_argument("--dry-run", action="store_true", help="extract + chunk only; no DB / embed")
    args = ap.parse_args()

    # Resolve + validate all paths up front (fail-closed).
    resolved = []
    for title, rel in DOCS:
        p = THJ_RESEARCH / rel
        if not p.is_file():
            raise SystemExit(f"FAIL-CLOSED: PDF not found: {p}")
        resolved.append((title, p))

    from kb_core.chunking import chunk_text

    if args.dry_run:
        for title, p in resolved:
            content = extract_pdf(p)
            n = len(chunk_text(content, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP))
            print(f"[policy] {title} — {len(content)} chars → {n} chunks  ({p.name})")
        return 0

    from kb_core import get_db, get_embedding

    total = 0
    with get_db() as conn:
        for title, p in resolved:
            content = extract_pdf(p)
            if not content.strip():
                raise SystemExit(f"FAIL-CLOSED: no text extracted from {p} (scanned/image PDF — needs OCR)")
            n = ingest_doc(conn, title, p, content, chunk_text, get_embedding)
            total += n
            print(f"[policy] ingested {title} — {n} chunks")
        conn.commit()
    print(f"[policy] done — {len(resolved)} docs, {total} chunks → reference_docs (category='{CATEGORY}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
