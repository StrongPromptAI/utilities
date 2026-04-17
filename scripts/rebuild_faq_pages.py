#!/usr/bin/env python3
"""Rebuild FAQ review pages from re-clustered QA pairs.

Splits into post_delivery (primary) and pre_delivery (secondary) sections.
Generates:
  - Individual FAQ markdown pages (dme/faq/faq-q{N}.md)
  - Hub page (dme/faq-review.md) with two sections
  - Updates roadmap.plans DB slugs

Usage:
    uv run python scripts/rebuild_faq_pages.py [--dry-run]
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from scripts.kb_core.config import DB_URL, ensure_model
from scripts.kb_core.ingest.faq import _get_patient_qa, _cluster_questions, _synthesize_faq_entry
from scripts.kb_core.db import get_db

PROJECT_ID = 19
FAQ_DIR = Path(os.path.expanduser(
    "~/repo_docs/utilities/plans/hj_roadmap/astro/src/content/docs/dme/faq"
))
HUB_PATH = FAQ_DIR.parent / "faq-review.md"


def get_patient_qa_by_stage(project_id: int) -> dict[str, list[dict]]:
    """Get patient QA pairs split by delivery_stage."""
    query = """
        SELECT qa.id, qa.question, qa.answer, qa.question_verbatim,
               qa.answer_verbatim, qa.topic, qa.category, qa.answered,
               qa.ingest_source_id, qa.delivery_stage,
               s.agent_name, s.source_date,
               e.caller_type
        FROM ingest_qa qa
        JOIN ingest_sources s ON qa.ingest_source_id = s.id
        LEFT JOIN ingest_evidence e ON e.ingest_source_id = qa.ingest_source_id
            AND e.project_id = qa.project_id
        WHERE qa.project_id = %s
        AND (e.caller_type IN ('patient', 'family_member') OR e.caller_type IS NULL)
        ORDER BY qa.id
    """
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(DB_URL, row_factory=dict_row)
    cur = conn.cursor()
    cur.execute(query, (project_id,))
    rows = cur.fetchall()
    conn.close()

    result = {"post_delivery": [], "pre_delivery": []}
    for r in rows:
        stage = r.get("delivery_stage") or "pre_delivery"
        result.setdefault(stage, []).append(r)
    return result


def cluster_and_synthesize(qa_pairs: list[dict], label: str, client) -> list[dict]:
    """Cluster QA pairs and synthesize FAQ entries."""
    if not qa_pairs:
        return []

    print(f"\n{'='*60}")
    print(f"Clustering {label}: {len(qa_pairs)} QA pairs")
    print(f"{'='*60}")
    sys.stdout.flush()

    clusters = _cluster_questions(qa_pairs, min_cluster_size=2)
    print(f"  {len(clusters)} clusters found")
    sys.stdout.flush()

    faq = []
    for i, cluster in enumerate(clusters):
        print(f"  [{i+1}/{len(clusters)}] Synthesizing ({len(cluster)} pairs)...", end=" ")
        sys.stdout.flush()

        entry = _synthesize_faq_entry(cluster, client)

        # Collect citations and metadata
        sources = {}
        topics = set()
        for qa in cluster:
            sid = qa["ingest_source_id"]
            if sid not in sources:
                sources[sid] = {
                    "source_id": sid,
                    "agent_name": qa["agent_name"],
                    "source_date": str(qa["source_date"]) if qa["source_date"] else None,
                    "verbatim": qa["question_verbatim"],
                }
            topics.add(qa["topic"])

        faq.append({
            "question": entry["faq_question"],
            "answer": entry["faq_answer"],
            "confidence": entry.get("confidence", 0),
            "frequency": len(cluster),
            "unique_calls": len(sources),
            "topics": sorted(topics),
            "citations": list(sources.values()),
        })
        print(f'"{entry["faq_question"][:60]}..."')
        sys.stdout.flush()

    # Sort by frequency
    faq.sort(key=lambda f: f["frequency"], reverse=True)
    return faq


def write_faq_page(faq_entry: dict, number: int, stage_label: str) -> str:
    """Write a single FAQ markdown page. Returns the filename."""
    filename = f"faq-q{number}.md"
    topics = ", ".join(faq_entry["topics"])
    confidence = int(faq_entry["confidence"] * 100)

    # Build citations (max 3 shown)
    citation_lines = []
    for c in faq_entry["citations"][:3]:
        verbatim = c["verbatim"][:80] if c["verbatim"] else ""
        citation_lines.append(
            f'- Source {c["source_id"]} ({c["agent_name"]}, {c["source_date"]}): "{verbatim}"'
        )
    citations = "\n".join(citation_lines)

    content = f"""---
title: "FAQ Q{number}"
sidebar:
  hidden: true
---

**Stage:** {stage_label}<br>
**Topic:** {topics}<br>
**Frequency:** {faq_entry["frequency"]} Q&A pairs from {faq_entry["unique_calls"]} calls<br>
**Confidence:** {confidence}%

---

## Question

> {faq_entry["question"]}

## Draft Answer

{faq_entry["answer"]}

---

## Source Citations

{citations}

---

*Review this draft answer using the feedback bubble above. Is it accurate? What would you change?*
"""
    filepath = FAQ_DIR / filename
    filepath.write_text(content)
    return filename


def write_hub_page(
    post_faq: list[dict],
    pre_faq: list[dict],
    total_calls: int,
    post_qa_count: int,
    pre_qa_count: int,
    post_start: int,
    pre_start: int,
):
    """Write the FAQ review hub page with post/pre delivery sections."""
    now = datetime.now(timezone.utc).strftime("%B %d, %Y at %I:%M %p UTC")

    # Build post-delivery table rows
    post_rows = []
    for i, entry in enumerate(post_faq):
        n = post_start + i
        q = entry["question"]
        freq = f'{entry["frequency"]} pairs, {entry["unique_calls"]} calls'
        post_rows.append(
            f'| {n} | [{q}](/dme/faq/faq-q{n}) | {freq} | [Review →](/dme/faq/faq-q{n}) |'
        )
    post_table = "\n".join(post_rows) if post_rows else "| — | No post-delivery FAQ entries yet | — | — |"

    # Build pre-delivery table rows
    pre_rows = []
    for i, entry in enumerate(pre_faq):
        n = pre_start + i
        q = entry["question"]
        freq = f'{entry["frequency"]} pairs, {entry["unique_calls"]} calls'
        pre_rows.append(
            f'| {n} | [{q}](/dme/faq/faq-q{n}) | {freq} | [Review →](/dme/faq/faq-q{n}) |'
        )
    pre_table = "\n".join(pre_rows) if pre_rows else "| — | No pre-delivery FAQ entries yet | — | — |"

    content = f"""---
title: "FAQ Review — Patient Chat Agent"
---

**Updated:** <small>{now}</small>

---

## How This Was Built

We analyzed **{total_calls} OrthoXpress customer service call recordings** using a local AI model (Mistral Small 3.2). The pipeline:

1. **Ingested** all {total_calls} calls with speaker diarization (Speaker 1 / Speaker 2)
2. **Classified** each call by category (order management, referral, tracking, insurance, equipment, billing, other)
3. **Identified speakers** — matched agent names from filenames to speaker IDs in transcripts
4. **Extracted Q&A pairs** — every distinct question a caller asked and how the agent answered
5. **Filtered to patients and family members only** — excluded insurance reps and provider office calls
6. **Tagged delivery stage** — classified each question as post-delivery (patient has equipment) or pre-delivery (waiting for equipment)
7. **Clustered similar questions** and synthesized one FAQ entry per cluster

**What was filtered out:** Insurance company calls, provider office calls (NPI lookups, referral submissions), and agent verification questions (date of birth, name spelling).

---

## How to Review

Each FAQ entry has a feedback bubble (💬). Tap it and tell us:
- **Is the question right?** Does this match what patients actually ask?
- **Is the answer right?** Is this what you'd tell a patient?
- **What's missing?** What would you add or change?

You can see your submitted feedback — it shows as 🟡 (pending review) or 🟢 (incorporated into the next draft).

---

## After Equipment Arrives (Post-Delivery)

These are questions patients ask **after they have their equipment**. This is the primary target for the chat agent — patients who are already provisioned and need help with what they have.

**{post_qa_count} Q&A pairs** from patient/family callers.

| # | Question | Frequency | Review |
|---|----------|-----------|--------|
{post_table}

---

## Before Equipment Arrives (Pre-Delivery)

These are questions patients ask **while waiting for equipment** — order status, referral process, insurance, delivery tracking. Important call volume data, but secondary priority for the chat agent.

**{pre_qa_count} Q&A pairs** from patient/family callers.

| # | Question | Frequency | Review |
|---|----------|-----------|--------|
{pre_table}
"""
    HUB_PATH.write_text(content)


def sync_db_slugs(total_faq: int, dry_run: bool = False):
    """Add/remove FAQ slugs in roadmap.plans to match the new count."""
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(DB_URL, row_factory=dict_row)
    cur = conn.cursor()

    # Get existing FAQ slugs
    cur.execute("SELECT id, slug FROM roadmap.plans WHERE slug LIKE 'faq-q%' ORDER BY id")
    existing = cur.fetchall()
    existing_nums = {int(r["slug"].replace("faq-q", "")): r["id"] for r in existing}

    needed = set(range(1, total_faq + 1))
    have = set(existing_nums.keys())

    to_add = needed - have
    to_remove = have - needed

    if dry_run:
        print(f"  DB slugs: {len(have)} existing, need {len(needed)}")
        if to_add:
            print(f"  Would ADD: {sorted(to_add)}")
        if to_remove:
            print(f"  Would REMOVE: {sorted(to_remove)}")
        conn.close()
        return

    # Remove excess
    for n in to_remove:
        cur.execute("DELETE FROM roadmap.plans WHERE id = %s", (existing_nums[n],))

    # Add missing
    for n in sorted(to_add):
        cur.execute(
            """INSERT INTO roadmap.plans
               (project_id, slug, title, audience, status,
                job_to_be_done, pain, value)
               VALUES (8, %s, %s, 'dme', 'planned',
                'FAQ review entry', 'Draft needs validation', 'Validated FAQ')""",
            (f"faq-q{n}", f"FAQ Q{n}"),
        )

    conn.commit()
    conn.close()
    print(f"  DB: removed {len(to_remove)}, added {len(to_add)} slugs")


def main():
    parser = argparse.ArgumentParser(description="Rebuild FAQ review pages")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    args = parser.parse_args()

    # Get QA pairs split by stage
    print("Loading patient QA pairs by delivery stage...")
    by_stage = get_patient_qa_by_stage(PROJECT_ID)
    post_qa = by_stage.get("post_delivery", [])
    pre_qa = by_stage.get("pre_delivery", [])
    print(f"  post_delivery: {len(post_qa)} pairs")
    print(f"  pre_delivery: {len(pre_qa)} pairs")

    # Get total call count
    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(DB_URL, row_factory=dict_row)
    cur = conn.cursor()
    cur.execute("SELECT count(*) as n FROM ingest_sources WHERE project_id = %s", (PROJECT_ID,))
    total_calls = cur.fetchone()["n"]
    conn.close()
    print(f"  Total calls: {total_calls}")

    if args.dry_run:
        print("\n[DRY RUN] Would cluster and rebuild. Exiting.")
        return

    # Ensure LM Studio model loaded
    ensure_model()

    from openai import OpenAI
    from scripts.kb_core.config import LM_STUDIO_URL
    client = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    # Cluster each stage separately
    post_faq = cluster_and_synthesize(post_qa, "POST-DELIVERY", client)
    pre_faq = cluster_and_synthesize(pre_qa, "PRE-DELIVERY", client)

    total_faq = len(post_faq) + len(pre_faq)
    print(f"\nTotal FAQ entries: {len(post_faq)} post + {len(pre_faq)} pre = {total_faq}")

    # Clear old FAQ pages
    if FAQ_DIR.exists():
        shutil.rmtree(FAQ_DIR)
    FAQ_DIR.mkdir(parents=True, exist_ok=True)

    # Write pages — post-delivery first (numbered 1..N), then pre-delivery (N+1..M)
    post_start = 1
    pre_start = len(post_faq) + 1

    print("\nWriting FAQ pages...")
    for i, entry in enumerate(post_faq):
        n = post_start + i
        write_faq_page(entry, n, "After Equipment Arrives (Post-Delivery)")

    for i, entry in enumerate(pre_faq):
        n = pre_start + i
        write_faq_page(entry, n, "Before Equipment Arrives (Pre-Delivery)")

    print(f"  Wrote {total_faq} FAQ pages")

    # Write hub page
    write_hub_page(post_faq, pre_faq, total_calls, len(post_qa), len(pre_qa),
                   post_start, pre_start)
    print(f"  Wrote hub page: {HUB_PATH}")

    # Sync DB slugs
    print("\nSyncing DB slugs...")
    sync_db_slugs(total_faq)

    # Export raw data for reference
    export = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_calls": total_calls,
        "post_delivery": [
            {"rank": i+1, **e} for i, e in enumerate(post_faq)
        ],
        "pre_delivery": [
            {"rank": len(post_faq)+i+1, **e} for i, e in enumerate(pre_faq)
        ],
    }
    export_path = FAQ_DIR.parent / "faq-data.json"
    with open(export_path, "w") as f:
        json.dump(export, f, indent=2, default=str)
    print(f"  Exported data: {export_path}")

    print(f"\nDone. {total_faq} FAQ entries ready for review.")
    print("Next: build and deploy (see hj-roadmap-publish skill)")


if __name__ == "__main__":
    main()
