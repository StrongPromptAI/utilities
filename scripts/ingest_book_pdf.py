"""Ingest a PDF (incl. scanned books) into the KB reference_docs corpus, with
figures preserved — the graphics-aware counterpart to ingest_book_epub.py.

Pipeline (doc_ingest core → KB adapter):
  MinerU extract → IR → structural chunk (image↔text association) →
  figures uploaded to the coach service volume → reference_docs upsert +
  reference_doc_chunks (inline figure links) + in-process ONNX embeddings.

Run (utilities 3.13 venv for the code; MinerU is a 3.12 subprocess):
  MINERU_BIN=~/repos/utilities/scripts/doc_ingest/.venv/bin/mineru \
  COACH_UPLOAD_SECRET=<secret> \
  uv run python scripts/ingest_book_pdf.py "<book>.pdf" <category> "<Title>" \
      --figures-url https://<coach-svc>.up.railway.app

Batch a large book by extracting once over all pages (MinerU windows internally);
--method-dir reuses an existing extraction (skip the multi-minute MinerU run).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # scripts/ on path

from doc_ingest.extract import extract, extract_batched, parse_extraction
from doc_ingest.targets.base import DocMeta, run_ingest
from doc_ingest.targets.kb import HttpUploadUploader, KBTarget


def _mint_upload_token(secret: str, ttl: int = 1800) -> str:
    import jwt
    return jwt.encode(
        {"aud": "coach-upload", "exp": int(time.time()) + ttl},
        secret, algorithm="HS256",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a PDF into KB reference_docs with figures.")
    ap.add_argument("source", help="path to the PDF (or, with --method-dir, the source label)")
    ap.add_argument("category", help="reference_docs.category")
    ap.add_argument("title", help="reference_docs.title (upsert key with category)")
    ap.add_argument("--figures-url", required=True, help="coach service base URL (serves /figures)")
    ap.add_argument("--method-dir", help="reuse an existing MinerU extraction dir (skip extract)")
    ap.add_argument("-s", "--start", type=int, help="0-indexed first page (single-shot)")
    ap.add_argument("-e", "--end", type=int, help="0-indexed last page (single-shot)")
    ap.add_argument("--batch-pages", type=int, default=60,
                    help="extract in resumable page-range batches of this size (0 = single-shot). "
                         "Default 60 — NEVER do a monolithic all-or-nothing run on a large book.")
    ap.add_argument("--out", default="/tmp/claude/book_extract", help="MinerU output dir")
    args = ap.parse_args()

    secret = os.environ.get("COACH_UPLOAD_SECRET", "")
    if not secret:
        print("COACH_UPLOAD_SECRET not set — required to upload figures to the coach service.")
        return 2

    if args.method_dir:
        ext = parse_extraction(args.method_dir, source_pdf=args.source)
    elif args.batch_pages and not (args.start is not None or args.end is not None):
        print(f"Extracting {Path(args.source).name} in resumable {args.batch_pages}-page batches…")
        ext = extract_batched(args.source, args.out, batch_pages=args.batch_pages)
    else:
        print(f"Extracting {Path(args.source).name} via MinerU (single-shot)…")
        ext = extract(args.source, args.out, start=args.start, end=args.end)
    print(f"  {ext.page_count} pages, {len(ext.blocks)} blocks")

    target = KBTarget(
        uploader=HttpUploadUploader(args.figures_url, _mint_upload_token(secret)),
        # db_factory defaults to kb_core.get_db (real KB Postgres)
    )
    doc = DocMeta(title=args.title, category=args.category,
                  source_file=Path(args.source).name, markdown=ext.markdown)

    res = run_ingest(ext, target, doc)
    print(f"\n✓ doc_id={res.doc_id}  chunks={res.chunk_count}  figures={res.image_count}")
    print(f"  category={args.category!r}  figures served at {args.figures_url}/figures/<{doc.title and ''}…>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
