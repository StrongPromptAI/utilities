#!/usr/bin/env python3
"""Batch ingest CS call .dote files into ingest_sources pipeline.

Usage:
    uv run python scripts/ingest_cs_batch.py /path/to/dote/dir [--limit N]

Pipeline per file:
  1. Parse .dote JSON → raw_text + agent_name from filename
  2. create_ingest_source (dedup by source_file)
  3. pretag_diarized → speaker attribution (deterministic for diarized)
  4. scrub_pii → store as tagged_text
  5. classify_source → category + in_scope
  6. extract_qa → QA pairs with delivery_stage
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

from scripts.kb_core.config import ensure_model
from scripts.kb_core.ingest.crud import create_ingest_source, get_ingest_source_by_file
from scripts.kb_core.ingest.pretag import pretag_diarized, scrub_pii
from scripts.kb_core.ingest.classify import classify_source
from scripts.kb_core.ingest.extract_qa import extract_qa_from_source
from scripts.kb_core.db import get_db

ORG_ID = 23       # OrthoXpress
PROJECT_ID = 19   # CS call analysis project


def parse_dote(file_path: str) -> dict:
    """Parse a .dote file into raw_text and metadata."""
    with open(file_path) as f:
        data = json.load(f)

    lines = data.get("lines", [])
    if not lines:
        return {"raw_text": "", "segment_count": 0}

    # Build raw text with speaker prefixes
    raw_parts = []
    for line in lines:
        speaker = line.get("speakerDesignation", "Unknown")
        text = line.get("text", "").strip()
        if text:
            raw_parts.append(f"[{speaker}] {text}")

    return {
        "raw_text": "\n".join(raw_parts),
        "segment_count": len(lines),
    }


def extract_agent_name(filename: str) -> str:
    """Extract agent name from filename like '[Jennifer Workman]_5529-...'."""
    m = re.match(r'\[([^\]]+)\]', filename)
    return m.group(1) if m else ""


def extract_call_date(filename: str) -> date | None:
    """Extract date from filename like '..._20260409182300(2906).dote'."""
    m = re.search(r'_(\d{4})(\d{2})(\d{2})\d{6}\(\d+\)\.dote$', filename)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def ingest_one(file_path: str, client=None) -> dict:
    """Full pipeline for one .dote file."""
    filename = os.path.basename(file_path)
    abs_path = os.path.abspath(file_path)

    # Dedup check
    existing = get_ingest_source_by_file(abs_path)
    if existing:
        return {"status": "skip", "reason": "already ingested", "id": existing["id"]}

    # Parse
    parsed = parse_dote(file_path)
    if not parsed["raw_text"]:
        return {"status": "skip", "reason": "empty transcript"}

    agent_name = extract_agent_name(filename)
    call_date = extract_call_date(filename)

    # 1. Create source record
    source_id = create_ingest_source(
        org_id=ORG_ID,
        project_id=PROJECT_ID,
        source_type="cs_call",
        source_file=abs_path,
        raw_text=parsed["raw_text"],
        source_date=call_date,
        agent_name=agent_name,
        segment_count=parsed["segment_count"],
    )

    # 2. Pretag (deterministic for diarized) + PII scrub
    tagged = pretag_diarized(parsed["raw_text"], agent_name)
    scrubbed = scrub_pii(tagged)

    # Store tagged_text
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ingest_sources SET tagged_text = %s WHERE id = %s",
                (scrubbed, source_id),
            )
        conn.commit()

    # 3. Classify (LLM)
    classify_result = classify_source(source_id, client=client)

    # 4. Extract QA with delivery_stage (LLM)
    qa_result = extract_qa_from_source(source_id, client=client)
    qa_count = len(qa_result.get("qa_pairs", []))

    # Store QA pairs
    if "error" not in qa_result:
        evidence = qa_result["classification_evidence"]
        qa_pairs = qa_result["qa_pairs"]

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ingest_evidence
                       (ingest_source_id, project_id, category, caller_type, key_quotes, rationale)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (source_id, PROJECT_ID, evidence["category_confirmed"],
                     evidence.get("caller_type"), json.dumps(evidence["key_quotes"]),
                     evidence["rationale"]),
                )
                for qa in qa_pairs:
                    cur.execute(
                        """INSERT INTO ingest_qa
                           (ingest_source_id, project_id, question, answer,
                            question_verbatim, answer_verbatim, topic, category,
                            answered, delivery_stage)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (source_id, PROJECT_ID, qa["question"], qa["answer"],
                         qa["question_verbatim"], qa["answer_verbatim"],
                         qa["topic"], classify_result.get("category", ""),
                         qa["answered"], qa.get("delivery_stage")),
                    )
            conn.commit()

    return {
        "status": "ok",
        "source_id": source_id,
        "agent": agent_name,
        "category": classify_result.get("category"),
        "qa_count": qa_count,
    }


def main():
    parser = argparse.ArgumentParser(description="Batch ingest CS .dote files")
    parser.add_argument("directory", help="Directory containing .dote files")
    parser.add_argument("--limit", type=int, help="Max files to process")
    args = parser.parse_args()

    # Find .dote files (exclude processed/ subdirectory)
    dote_dir = Path(args.directory)
    files = sorted([
        f for f in dote_dir.glob("*.dote")
        if "processed" not in str(f)
    ])

    if args.limit:
        files = files[:args.limit]

    print(f"Found {len(files)} .dote files to process")
    if not files:
        return

    # Ensure model is loaded
    ensure_model()

    from openai import OpenAI
    from scripts.kb_core.config import LM_STUDIO_URL
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    ok = skip = err = 0
    for i, f in enumerate(files):
        print(f"\n[{i+1}/{len(files)}] {f.name}")
        sys.stdout.flush()
        try:
            result = ingest_one(str(f), client=client)
            if result["status"] == "ok":
                print(f"  ✓ id={result['source_id']} {result['category']} {result['qa_count']} QA")
                ok += 1
            elif result["status"] == "skip":
                print(f"  → {result['reason']}")
                skip += 1
        except Exception as e:
            print(f"  ✗ ERROR: {e}")
            err += 1
        sys.stdout.flush()

    print(f"\nDone: {ok} ingested, {skip} skipped, {err} errors")


if __name__ == "__main__":
    main()
