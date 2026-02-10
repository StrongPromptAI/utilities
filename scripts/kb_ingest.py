#!/usr/bin/env python3
"""
Knowledge Base Ingestion Script

Ingests transcripts (DOCX, CSV, plaintext) into the knowledge base.
Uses kb_core for all chunking, embedding, and CRUD operations.

All entity references use database IDs to prevent duplicates from typos.
"""

import argparse
from pathlib import Path
from datetime import date

from scripts.kb_core import (
    preprocess_transcript,
    chunk_transcript,
    chunk_by_sections,
    get_call_by_source_file,
    create_call,
    insert_chunks,
    add_contacts_to_call,
)
from scripts.kb_core.db import get_db


def _validate_ids(org_id: int, project_id: int = None, contact_ids: list[int] = None) -> dict:
    """Validate that all IDs exist in the database. Returns names for confirmation output."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM orgs WHERE id = %s", (org_id,))
            org = cur.fetchone()
            if not org:
                return {"error": f"Org id={org_id} not found"}

            project = None
            if project_id:
                cur.execute("SELECT id, name FROM projects WHERE id = %s", (project_id,))
                project = cur.fetchone()
                if not project:
                    return {"error": f"Project id={project_id} not found"}

            contacts = []
            if contact_ids:
                placeholders = ",".join(["%s"] * len(contact_ids))
                cur.execute(
                    f"SELECT id, name FROM contacts WHERE id IN ({placeholders}) ORDER BY name",
                    contact_ids,
                )
                contacts = cur.fetchall()
                found_ids = {c["id"] for c in contacts}
                missing = [cid for cid in contact_ids if cid not in found_ids]
                if missing:
                    return {"error": f"Contact ids not found: {missing}"}

    return {
        "org": org,
        "project": project,
        "contacts": contacts,
    }


def ingest(
    file_path: str,
    org_id: int,
    call_date: date,
    contact_ids: list[int] = None,
    source_type: str = "call_transcript",
    summary: str = None,
    project_id: int = None,
) -> dict:
    """Ingest a transcript into the knowledge base."""

    path = Path(file_path)
    if not path.exists():
        print(f"Error: File not found: {file_path}")
        return {"error": f"File not found: {file_path}"}

    # Validate all IDs before touching anything
    validated = _validate_ids(org_id, project_id, contact_ids)
    if "error" in validated:
        print(f"Error: {validated['error']}")
        return validated

    # Print what we're about to do
    print(f"Org:      {validated['org']['name']} (id={org_id})")
    if validated["project"]:
        print(f"Project:  {validated['project']['name']} (id={project_id})")
    if validated["contacts"]:
        names = [f"{c['name']} (id={c['id']})" for c in validated["contacts"]]
        print(f"Contacts: {', '.join(names)}")

    # Duplicate check
    existing = get_call_by_source_file(str(path))
    if existing:
        print(f"Already ingested as call {existing['id']} ({existing.get('chunk_count', '?')} chunks)")
        return {"error": "duplicate", "call_id": existing["id"]}

    # Preprocess (format detection + filler filtering)
    print(f"Preprocessing {path.name}...")
    result = preprocess_transcript(str(path))
    print(f"  Format: {result['format']}")
    print(f"  Filtered: {result['filtered_count']} obvious + {result['llm_filtered_count']} LLM")
    print(f"  Participants: {', '.join(result['participants'])}")

    # Chunk by speaker turns (or sections for meeting notes)
    if source_type == 'meeting_notes':
        chunks = chunk_by_sections(result['text'])
    else:
        chunks = chunk_transcript(result['text'])

    print(f"  Chunks: {len(chunks)}")

    # Create call
    call_id = create_call(
        call_date=call_date,
        org_id=org_id,
        source_type=source_type,
        source_file=str(path),
        summary=summary,
        project_id=project_id,
    )

    # Link contacts to call via junction table
    if contact_ids:
        add_contacts_to_call(call_id, contact_ids)

    # Insert chunks with embeddings
    print("Embedding and inserting chunks...")
    count = insert_chunks(call_id, chunks)

    print(f"Ingested: call {call_id}, {count} chunks, {result['filtered_count']} + {result['llm_filtered_count']} fillers removed")
    print(f"\n  Next: \"Generate summaries for call {call_id}\" · \"Generate summaries and harvest call {call_id}\"")
    return {
        "call_id": call_id,
        "org_id": org_id,
        "project_id": project_id,
        "chunks_indexed": count,
        "filtered_count": result["filtered_count"],
        "llm_filtered_count": result["llm_filtered_count"],
        "format": result["format"],
        "file": str(path),
    }


def main():
    parser = argparse.ArgumentParser(description="Knowledge Base Ingestion")
    subparsers = parser.add_subparsers(dest="command")

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a transcript")
    ingest_parser.add_argument("file", help="Path to transcript (DOCX, CSV, or plaintext)")
    ingest_parser.add_argument("--org-id", required=True, type=int, help="Organization ID")
    ingest_parser.add_argument("--date", required=True, help="Call date (YYYY-MM-DD)")
    ingest_parser.add_argument("--contact-ids", help="Comma-separated contact IDs")
    ingest_parser.add_argument("--project-id", type=int, help="Project ID")
    ingest_parser.add_argument("--summary", help="Brief summary")
    ingest_parser.add_argument("--source-type", default="call_transcript", choices=["call_transcript", "podcast", "verbal_recap"])

    args = parser.parse_args()

    if args.command == "ingest":
        contact_ids = None
        if args.contact_ids:
            contact_ids = [int(x.strip()) for x in args.contact_ids.split(",")]

        ingest(
            file_path=args.file,
            org_id=args.org_id,
            call_date=date.fromisoformat(args.date),
            contact_ids=contact_ids,
            summary=args.summary,
            source_type=args.source_type,
            project_id=args.project_id,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
