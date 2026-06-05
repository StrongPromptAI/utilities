#!/usr/bin/env python3
"""Extract an EPUB to text and ingest it as a reference_docs corpus on the Railway KB DB.

For the methodology *books* that back the sales coach (CTWTCS = sales_framework;
The Expansion Sale = sales_expansion). Books are ad-hoc ingests (not repo markdown),
so they live here rather than in `ingest_roadmap_corpus.py`. Upserts by (title, category):
re-running replaces the doc's chunks in place. Writes straight to Railway (kb_core DB_URL);
embeddings compute locally via ONNX.

Usage:
  uv run python scripts/ingest_book_epub.py "<path.epub>" <category> "<Title>"
"""
import html
import re
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kb_core import get_db, chunk_by_sections, get_embedding

_SKIP = ("toc", "nav", "cover", "copyright", "title")


def epub_to_text(epub_path: str) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(epub_path) as z:
        names = sorted(
            n for n in z.namelist()
            if n.lower().endswith((".xhtml", ".html", ".htm"))
            and not any(s in n.lower() for s in _SKIP)
        )
        for n in names:
            raw = z.read(n).decode("utf-8", errors="replace")
            raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
            txt = re.sub(r"(?s)<[^>]+>", " ", raw)        # strip tags
            txt = html.unescape(txt)
            txt = re.sub(r"[ \t]+", " ", txt)
            txt = re.sub(r"\n\s*\n\s*\n+", "\n\n", txt)
            if len(txt.strip()) > 40:
                parts.append(txt.strip())
    return "\n\n".join(parts)


def ingest_book(epub_path: str, category: str, title: str) -> tuple[int, int]:
    text = epub_to_text(epub_path)
    print(f"extracted {len(text)} chars from {Path(epub_path).name}")
    chunks = chunk_by_sections(text, min_chunk_size=100)
    print(f"generated {len(chunks)} chunks")
    src = Path(epub_path).name
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM reference_docs WHERE title=%s AND category=%s",
                (title, category),
            )
            row = cur.fetchone()
            if row:
                doc_id = row["id"]
                cur.execute(
                    "UPDATE reference_docs SET content=%s, source_file=%s WHERE id=%s",
                    (text, src, doc_id),
                )
                print(f"updated reference doc {doc_id}")
            else:
                cur.execute(
                    "INSERT INTO reference_docs (title, category, content, source_file) "
                    "VALUES (%s,%s,%s,%s) RETURNING id",
                    (title, category, text, src),
                )
                doc_id = cur.fetchone()["id"]
                print(f"created reference doc {doc_id}")
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reference_doc_chunks WHERE doc_id=%s", (doc_id,))
        for idx, ch in enumerate(chunks):
            emb = get_embedding(ch)
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO reference_doc_chunks (doc_id, chunk_idx, text, embedding) "
                    "VALUES (%s,%s,%s,%s)",
                    (doc_id, idx, ch, emb),
                )
            if (idx + 1) % 50 == 0:
                print(f"  embedded {idx + 1}/{len(chunks)}")
        conn.commit()
    return doc_id, len(chunks)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    doc_id, n = ingest_book(sys.argv[1], sys.argv[2], sys.argv[3])
    print(f"\n✓ ingested doc_id={doc_id} as category={sys.argv[2]!r} with {n} chunks")
    print("Next: ALTER settings add kb_sales_expansion_doc_ids + populate from category.")
