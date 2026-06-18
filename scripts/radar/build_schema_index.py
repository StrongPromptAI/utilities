"""
Build a per-repo schema-comment index for the radar (plan thj/26-6-16).

Reads a registered repo's committed ``schema.sql``, extracts one chunk per
commented table (via ``schema_corpus``), embeds each chunk, and writes:

  ~/.claude/radar_schema_<slug>.json           — {repo, schema_sql_path,
                                                   schema_mtime, config_signature,
                                                   chunks:[{table, text, embedding}]}
  ~/.claude/radar_schema_<slug>_manifest.json  — small mtime/count record the
                                                  prompt-hook freshness gate reads.

Embed backend: embed_client.py (local utilities ONNX service by default).

Usage:
    uv run --project ~/repos/utilities python \
        ~/repos/utilities/scripts/radar/build_schema_index.py --repo thj

Exit codes: 0 ok (or nothing-to-do); 2 bad repo / missing schema.sql / embed
service unreachable. Mirrors build_index.py's posture: loud on config errors,
since this is run deliberately, not in the hot prompt path.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from embed_client import EMBED_URL, embed as _embed, wait_for_ready
import schema_corpus as sc

DOCUMENT_PREFIX = "search_document: "
BATCH = 20


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the schema-comment radar index for a repo.")
    ap.add_argument("--repo", required=True, help="repo slug registered in ~/.claude/radar_schema_repos.json")
    args = ap.parse_args()

    cfg = sc.load_repos().get(args.repo)
    if not cfg:
        print(f"ERROR: repo {args.repo!r} not in {sc.REPOS_REGISTRY}", file=sys.stderr)
        return 2
    schema_path = sc.expand(cfg.get("schema_sql", ""))
    if not schema_path or not Path(schema_path).exists():
        print(f"ERROR: schema.sql not found for {args.repo!r}: {schema_path}", file=sys.stderr)
        return 2

    chunks = sc.build_chunks(schema_path)
    if not chunks:
        print(f"[build_schema_index] {args.repo}: no substantive table comments — nothing to index.")
        return 0

    print(f"[build_schema_index] {args.repo}: {len(chunks)} commented tables | embed {EMBED_URL}")

    embeddings: list[list[float]] = []
    try:
        wait_for_ready(batch=True)  # wake embed-batch if remote/hibernating
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i : i + BATCH]
            vecs = _embed([DOCUMENT_PREFIX + c["text"] for c in batch], batch=True, timeout=30.0)
            embeddings.extend(vecs)
            print(f"  embedded {min(i + BATCH, len(chunks))}/{len(chunks)}")
    except Exception as e:
        print(f"ERROR: embed service unreachable: {e}", file=sys.stderr)
        return 2

    for c, v in zip(chunks, embeddings):
        c["embedding"] = v

    mtime = Path(schema_path).stat().st_mtime
    index = {
        "repo": args.repo,
        "schema_sql_path": schema_path,
        "schema_mtime": mtime,
        "config_signature": sc.CONFIG_SIGNATURE,
        "chunks": chunks,
    }
    sc.index_path(args.repo).parent.mkdir(parents=True, exist_ok=True)
    sc.index_path(args.repo).write_text(json.dumps(index, separators=(",", ":")))
    sc.manifest_path(args.repo).write_text(json.dumps({
        "schema_sql_path": schema_path,
        "schema_mtime": mtime,
        "config_signature": sc.CONFIG_SIGNATURE,
        "chunk_count": len(chunks),
    }, indent=2))

    print(f"Wrote {len(chunks)} schema chunks → {sc.index_path(args.repo)}")
    print(f"Manifest: {sc.manifest_path(args.repo)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
