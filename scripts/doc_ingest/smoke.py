"""Core + adapter smoke tests (PY-1, PY-1b).

  # PY-1 — core round-trips a PDF/extraction to chunks with image refs
  python -m doc_ingest.smoke --method-dir <vlm_dir>          # parse existing extraction
  python -m doc_ingest.smoke --pdf <file>.pdf [-s N -e M]    # run MinerU then chunk

  # PY-1b — KB adapter round-trips offline (fake uploader + fake DB, no side effects)
  python -m doc_ingest.smoke --method-dir <vlm_dir> --target kb --offline

Offline mode injects fakes so the adapter's full path (chunk → stage_image →
enrich → embed → write) runs without touching oxp.files or Postgres — proving
the IngestTarget Protocol fits the adapter.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # scripts/ on path

from doc_ingest.chunk import chunk_extraction
from doc_ingest.extract import extract, parse_extraction


# ── offline fakes for PY-1b ──────────────────────────────────────────────────

def _fake_uploader(local_path: str, object_name: str) -> str:
    """No real upload — return the deterministic coach-service figure URL the real
    HttpUploadUploader would (kb-project volume; see plan 26-6-20)."""
    return f"https://coach-svc.up.railway.app/figures/{object_name}"


class _FakeCursor:
    def __init__(self, store): self._store = store; self._last = None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=()):
        s = sql.split()[0].upper()
        if s == "SELECT":
            self._last = None                       # force the INSERT branch (new doc)
        elif s == "INSERT" and "reference_docs" in sql:
            self._last = {"id": 9999}
        elif s == "INSERT" and "reference_doc_chunks" in sql:
            self._store["chunks"].append(params)
        elif s == "DELETE":
            self._store["deleted"] = True
    def fetchone(self): return self._last


class _FakeConn:
    def __init__(self, store): self._store = store
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def cursor(self): return _FakeCursor(self._store)
    def commit(self): self._store["committed"] = True


def _print_chunks(chunks) -> None:
    print(f"chunks: {len(chunks)} | {dict(Counter(c.chunk_type for c in chunks))}")
    imgs = [c for c in chunks if c.chunk_type == "image"]
    print(f"image chunks with caption+context: "
          f"{sum(1 for c in imgs if c.caption and len(c.text) > len(c.caption))}/{len(imgs)}")
    if any("section_type" in c.extra or "chat_value" in c.extra for c in chunks):
        print("WARNING: equipment classification leaked into core chunks!")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf")
    ap.add_argument("--method-dir")
    ap.add_argument("-s", "--start", type=int)
    ap.add_argument("-e", "--end", type=int)
    ap.add_argument("--target", choices=["kb"], help="offline adapter round-trip")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default="/tmp/claude/doc_ingest_smoke")
    args = ap.parse_args()

    if args.method_dir:
        ext = parse_extraction(args.method_dir, source_pdf=args.pdf or args.method_dir)
    elif args.pdf:
        ext = extract(args.pdf, args.out, start=args.start, end=args.end)
    else:
        print("need --method-dir or --pdf"); return 2

    print(f"=== PY-1 core === pages={ext.page_count} blocks={len(ext.blocks)}")
    chunks = chunk_extraction(ext, min_chars=100, max_chars=3000)
    _print_chunks(chunks)

    if args.target == "kb":
        if not args.offline:
            print("refusing live KB/oxp.files write from smoke; use --offline"); return 2
        from doc_ingest.targets.base import DocMeta, run_ingest
        from doc_ingest.targets.kb import KBTarget
        store = {"chunks": [], "deleted": False, "committed": False}
        target = KBTarget(uploader=_fake_uploader, db_factory=lambda: _FakeConn(store))
        doc = DocMeta(title="SMOKE Corporate Lifecycles", category="smoke",
                      source_file="smoke.pdf", markdown=ext.markdown)
        res = run_ingest(ext, target, doc)
        print(f"=== PY-1b KB offline === doc_id={res.doc_id} chunks={res.chunk_count} "
              f"images={res.image_count} committed={store['committed']}")
        linked = sum(1 for p in store["chunks"] if "/figures/" in p[2])
        print(f"chunks carrying a coach-service figure link: {linked}")
        assert res.chunk_count == len(store["chunks"]), "write/chunk count mismatch"
        assert linked == res.image_count, "image link count != image_count"
        print("PY-1b: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
