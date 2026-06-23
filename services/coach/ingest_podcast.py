#!/usr/bin/env python3
"""ingest_podcast.py — pull the Sales podcast transcripts (OFFICIAL method) into the
coach corpus. Recut-aware. Reuses kb_core (chunk + ONNX embed + DB).

Official pull (never scrape, never read local transcript files): GET the feed
(COACH_PODCAST_FEED_URL — code-bearing, env, NOT committed). The full transcript rides
the feed as RSS **`<content:encoded>`** (the server's canonical transcript carriage) —
that is the primary source (HTML → text). Fallback: the raw route
    <base>/{slug}/{code}/ep/{name}/transcript   (raw markdown)
(currently returns "no transcript" — the `-transcript.md` sidecar isn't populated; the
feed's content:encoded is the one that's live).

Recut-aware: upsert by (title, category='sales_podcast'). Transcript unchanged → SKIP
(no re-embed). Changed (recut) or new → re-chunk + re-embed. Episodes get re-recut, so
change is detected by comparing the freshly-pulled transcript to the stored content.

Run (from the utilities repo root):
  COACH_PODCAST_FEED_URL='https://podcast.strongprompt.ai/sales/<code>/feed.xml' \
    uv run python services/coach/ingest_podcast.py --dry-run   # list episodes, no DB
  COACH_PODCAST_FEED_URL=... uv run python services/coach/ingest_podcast.py
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))   # kb_core

CATEGORY = "sales_podcast"
FEED_URL = os.environ.get("COACH_PODCAST_FEED_URL", "")


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def fetch_feed(url: str) -> str:
    r = httpx.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.text


def parse_episodes(feed_xml: str) -> list[dict]:
    """Namespace-robust: pull <item> title + enclosure url + content:encoded (transcript)."""
    root = ET.fromstring(feed_xml)
    eps: list[dict] = []
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title = url = encoded = None
        for ch in item:
            lt = _local(ch.tag)
            if lt == "title" and ch.text:
                title = ch.text.strip()
            elif lt == "enclosure":
                url = ch.get("url")
            elif lt == "encoded" and ch.text:   # content:encoded
                encoded = ch.text
        if title and url:
            eps.append({"title": title, "enclosure": url, "encoded": encoded})
    return eps


def html_to_text(s: str) -> str:
    """Flatten the transcript HTML (markdown-rendered headings/paragraphs) to text."""
    s = re.sub(r"(?i)</(p|h[1-6]|li|div|tr)>", "\n\n", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def transcript_url(enclosure: str) -> str:
    """The official transcript route, derived from the enclosure: .../ep/<name>.mp3 → .../ep/<name>/transcript."""
    return enclosure[:-4] + "/transcript" if enclosure.endswith(".mp3") else enclosure.rstrip("/") + "/transcript"


def fetch_transcript(url: str) -> str | None:
    r = httpx.get(url, timeout=30, follow_redirects=True)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.text


def ingest() -> int:
    if not FEED_URL:
        raise SystemExit("FAIL-FAST: COACH_PODCAST_FEED_URL not set")
    from kb_core import chunk_text, get_db, get_embedding

    eps = parse_episodes(fetch_feed(FEED_URL))
    print(f"[podcast] {len(eps)} episodes in feed")
    new = recut = skipped = 0
    total_chunks = 0
    with get_db() as conn:
        for ep in eps:
            turl = transcript_url(ep["enclosure"])
            # Primary: the feed's content:encoded (live). Fallback: the raw /transcript route.
            md = html_to_text(ep["encoded"]) if ep.get("encoded") else fetch_transcript(turl)
            if not md:
                print(f"[podcast] no transcript for {ep['title']!r} — skip")
                continue
            title = ep["title"]
            with conn.cursor() as cur:
                cur.execute("SELECT id, content FROM reference_docs WHERE title=%s AND category=%s", (title, CATEGORY))
                row = cur.fetchone()
            if row and row["content"] == md:
                skipped += 1
                continue
            chunks = chunk_text(md, chunk_size=1000, overlap=120)   # size-based: flat transcript has no headers
            with conn.cursor() as cur:
                if row:
                    doc_id = row["id"]
                    recut += 1
                    cur.execute("UPDATE reference_docs SET content=%s, source_file=%s WHERE id=%s", (md, turl, doc_id))
                else:
                    new += 1
                    cur.execute(
                        "INSERT INTO reference_docs (title, category, content, source_file) VALUES (%s,%s,%s,%s) RETURNING id",
                        (title, CATEGORY, md, turl),
                    )
                    doc_id = cur.fetchone()["id"]
                cur.execute("DELETE FROM reference_doc_chunks WHERE doc_id=%s", (doc_id,))
                for i, ct in enumerate(chunks):
                    cur.execute(
                        "INSERT INTO reference_doc_chunks (doc_id, chunk_idx, text, embedding) VALUES (%s,%s,%s,%s)",
                        (doc_id, i, ct, get_embedding(ct)),
                    )
            total_chunks += len(chunks)
            print(f"[podcast] {'NEW' if not row else 'RECUT'} {title} — {len(chunks)} chunks")
        conn.commit()
    print(f"[podcast] done — new={new} recut={recut} unchanged(skipped)={skipped}, {total_chunks} chunks → category='{CATEGORY}'")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest the Sales podcast transcripts (official pull, recut-aware).")
    ap.add_argument("--dry-run", action="store_true", help="list episodes + transcript URLs; no DB")
    args = ap.parse_args()
    if not FEED_URL:
        raise SystemExit("FAIL-FAST: COACH_PODCAST_FEED_URL not set")
    if args.dry_run:
        eps = parse_episodes(fetch_feed(FEED_URL))
        print(f"[podcast] {len(eps)} episodes:")
        for e in eps:
            print(f"  - {e['title']}\n      ← {transcript_url(e['enclosure'])}")
        return 0
    return ingest()


if __name__ == "__main__":
    raise SystemExit(main())
