#!/usr/bin/env python3
"""harness_agent — local end-to-end driver for the coach agent (no HTTP, no auth).

Runs the real agentic tool-use loop against the live kb corpus, with:
  • embed_fn = kb_core local ONNX nomic 768d (same vector space as build_brain)
  • conn     = kb_core.get_db() (kb Postgres over the Railway public proxy)
  • zai_key  = ~/.config/keys.json → 'z.ai'  (GLM 5.2, the agentic model)
  • OpenRouter key (mine_practice_reviews web fetch) resolves from keys.json automatically.

Prints progress events inline ([…]) and streams the answer text, so the meeting-prep
read+opener can be read critically. Run from the utilities repo root:

  uv run python services/coach/harness_agent.py "prep me for Dr. John Andrawis in Torrance"
  uv run python services/coach/harness_agent.py "how do I handle a price objection from a surgeon?"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# kb_core lives in <repo>/scripts; the coach modules import as top-level (service layout).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# review_cache reads COACH_DB_URL / KB_DATABASE_URL at call time — point it at the kb proxy
# (same URL kb_core resolves) so the cache get/put work locally.
from kb_core.config import DB_URL as _KB_URL  # noqa: E402
os.environ.setdefault("KB_DATABASE_URL", _KB_URL)

from kb_core import get_db, get_embedding  # noqa: E402

import agent  # noqa: E402


def _zai_key() -> str:
    keys = json.loads(Path("~/.config/keys.json").expanduser().read_text())
    return keys["z.ai"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("message", help="the rep's message to the coach")
    ap.add_argument("--model", default=agent.MODEL)
    args = ap.parse_args()

    conn = get_db()
    try:
        print(f"\n>>> {args.message}\n", flush=True)
        async for ev in agent.run_agent(
            args.message, [],
            embed_fn=get_embedding, conn=conn, zai_key=_zai_key(), model=args.model,
        ):
            if ev["type"] == "progress":
                print(f"\n  [{ev['text']}]", flush=True)
            else:
                print(ev["text"], end="", flush=True)
        print("\n", flush=True)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
