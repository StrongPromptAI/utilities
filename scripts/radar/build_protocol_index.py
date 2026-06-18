"""
Build a per-repo protocol-component index for the radar (plan thj/26-6-16 Phase 2).

Reads live ``public.protocol_component`` (the promoted/live set), renders one
chunk per *section* (via ``protocol_corpus``), embeds each, and writes:

  ~/.claude/radar_protocol_<slug>.json           — {repo, watermark, content_hash,
                                                     config_signature,
                                                     chunks:[{component_key, protocol_id,
                                                              section, title, text, embedding}]}
  ~/.claude/radar_protocol_<slug>_manifest.json  — small record the prompt-hook
                                                    freshness gate reads (watermark
                                                    + content_hash + chunk_count).

The manifest carries the **live-DB watermark** — ``max(id)`` + promoted-row count
of ``sandbox.protocol_transaction WHERE promoted_at IS NOT NULL`` (where promotes
are recorded; public's own transaction table is a separate population). The
prompt-hook gate compares this to a no-DB-in-hot-path accessor, so a promote that
skips the rebuild hook becomes *detectable* (watermark mismatch), not silent.

Rebuild is **idempotent** — identical chunk content + signature ⇒ no re-embed
(bulk promotes coalesce) — and **lock-guarded** against concurrent fires.

``DATABASE_URL`` is required at build time ONLY (never in the hot prompt path).
Source is ``psql`` (no DB driver dependency in utilities; the build is a
deliberate offline script, not the hot path).

Usage:
    DATABASE_URL=postgresql://postgres@localhost:5433/hj_main \
      uv run --project ~/repos/utilities python \
      ~/repos/utilities/scripts/radar/build_protocol_index.py --repo thj

Exit codes: 0 ok / nothing-to-do; 2 missing DATABASE_URL / DB unreachable /
embed service unreachable. Loud on config errors (run deliberately, not hot-path).
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from embed_client import EMBED_URL, embed as _embed, wait_for_ready
import protocol_corpus as pc

DOCUMENT_PREFIX = "search_document: "
BATCH = 20
LOCK_TTL = 300  # a rebuild is in flight; don't stampede.

# Live content set: the promoted/live rows patients are served.
ROWS_SQL = (
    "SELECT coalesce(json_agg(row_to_json(t)), '[]') FROM ("
    "SELECT component_key, protocol_id, title, content_goal, clinical_patterns "
    "FROM public.protocol_component WHERE is_active) t"
)
# Watermark: promotes are recorded in sandbox.protocol_transaction.promoted_at.
WM_SQL = (
    "SELECT json_build_object("
    "'wm_id', coalesce(max(id),0), 'wm_count', count(*)) "
    "FROM sandbox.protocol_transaction WHERE promoted_at IS NOT NULL"
)


def _psql_json(db_url: str, sql: str):
    out = subprocess.run(
        ["psql", db_url, "-tAc", sql],
        capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "psql failed")
    return json.loads(out.stdout.strip() or "null")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the protocol-component radar index for a repo.")
    ap.add_argument("--repo", required=True, help="repo slug (index filename key)")
    args = ap.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL required at build time (build-only; never the hot path).", file=sys.stderr)
        return 2

    lock = Path.home() / ".claude" / f"radar_protocol_rebuild_{args.repo}.lock"
    if lock.exists() and (time.time() - lock.stat().st_mtime) < LOCK_TTL:
        print(f"[build_protocol_index] {args.repo}: rebuild already in flight; skip.")
        return 0

    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(time.time()))

        try:
            rows = _psql_json(db_url, ROWS_SQL)
            watermark = _psql_json(db_url, WM_SQL)
        except Exception as e:
            print(f"ERROR: DB read failed: {e}", file=sys.stderr)
            return 2

        chunks = pc.build_chunks_from_rows(rows or [])
        if not chunks:
            print(f"[build_protocol_index] {args.repo}: no substantive sections — nothing to index.")
            return 0

        content_hash = hashlib.sha256(
            json.dumps([c["text"] for c in chunks], ensure_ascii=False).encode()
        ).hexdigest()[:16]

        # Idempotent no-op: identical content + signature ⇒ skip the (expensive)
        # re-embed. Refresh only the watermark if it advanced on unchanged content.
        mpath = pc.manifest_path(args.repo)
        if mpath.exists():
            try:
                old = json.loads(mpath.read_text())
                if (old.get("content_hash") == content_hash
                        and old.get("config_signature") == pc.CONFIG_SIGNATURE):
                    if old.get("watermark") != watermark:
                        old["watermark"] = watermark
                        mpath.write_text(json.dumps(old, indent=2))
                    print(f"[build_protocol_index] {args.repo}: unchanged "
                          f"({len(chunks)} chunks); no re-embed.")
                    return 0
            except Exception:
                pass

        print(f"[build_protocol_index] {args.repo}: {len(chunks)} sections | embed {EMBED_URL}")
        embeddings: list[list[float]] = []
        try:
            wait_for_ready(batch=True)  # wake embed-batch if remote/hibernating
            for i in range(0, len(chunks), BATCH):
                batch = chunks[i:i + BATCH]
                embeddings.extend(_embed([DOCUMENT_PREFIX + c["text"] for c in batch], batch=True, timeout=30.0))
                print(f"  embedded {min(i + BATCH, len(chunks))}/{len(chunks)}")
        except Exception as e:
            print(f"ERROR: embed service unreachable: {e}", file=sys.stderr)
            return 2

        for c, v in zip(chunks, embeddings):
            c["embedding"] = v

        index = {
            "repo": args.repo,
            "watermark": watermark,
            "content_hash": content_hash,
            "config_signature": pc.CONFIG_SIGNATURE,
            "chunks": chunks,
        }
        pc.index_path(args.repo).parent.mkdir(parents=True, exist_ok=True)
        pc.index_path(args.repo).write_text(json.dumps(index, separators=(",", ":")))
        mpath.write_text(json.dumps({
            "watermark": watermark,
            "content_hash": content_hash,
            "config_signature": pc.CONFIG_SIGNATURE,
            "chunk_count": len(chunks),
        }, indent=2))

        print(f"Wrote {len(chunks)} protocol chunks → {pc.index_path(args.repo)}")
        print(f"Manifest: {mpath} | watermark {watermark}")
        return 0
    finally:
        try:
            lock.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
