#!/usr/bin/env python3
"""
Knowledge Base Ingestion Script

Ingests transcripts (DOCX, CSV, plaintext) into the knowledge base.
Uses kb_core for all chunking, embedding, and CRUD operations.
"""

import argparse
from pathlib import Path
from datetime import date

from scripts.kb_core import (
    preprocess_transcript,
    chunk_transcript,
    chunk_by_sections,
    get_or_create_stakeholder,
    get_call_by_source_file,
    create_call,
    insert_chunks,
)


def ingest(
    file_path: str,
    stakeholder_name: str,
    stakeholder_type: str,
    call_date: date,
    participants: list[str],
    source_type: str = "call_transcript",
    organization: str = None,
    summary: str = None,
) -> dict:
    """Ingest a transcript into the knowledge base."""

    path = Path(file_path)
    if not path.exists():
        print(f"Error: File not found: {file_path}")
        return {"error": f"File not found: {file_path}"}

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

    # Use detected participants if none provided
    if not participants and result['participants']:
        participants = result['participants']

    # Chunk by speaker turns (or sections for meeting notes)
    if source_type == 'meeting_notes':
        chunks = chunk_by_sections(result['text'])
    else:
        chunks = chunk_transcript(result['text'])

    print(f"  Chunks: {len(chunks)}")

    # Create stakeholder + call
    stakeholder_id = get_or_create_stakeholder(stakeholder_name, stakeholder_type, organization)
    call_id = create_call(
        call_date=call_date,
        participants=participants,
        stakeholder_id=stakeholder_id,
        source_type=source_type,
        source_file=str(path),
        summary=summary,
    )

    # Insert chunks with embeddings
    print("Embedding and inserting chunks...")
    count = insert_chunks(call_id, chunks)

    print(f"Ingested: call {call_id}, {count} chunks, {result['filtered_count']} + {result['llm_filtered_count']} fillers removed")
    return {
        "call_id": call_id,
        "stakeholder": stakeholder_name,
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
    ingest_parser.add_argument("--stakeholder", required=True, help="Stakeholder name")
    ingest_parser.add_argument("--type", required=True, choices=["doctor", "partner", "vendor", "investor", "employee", "prospect", "other"])
    ingest_parser.add_argument("--date", required=True, help="Call date (YYYY-MM-DD)")
    ingest_parser.add_argument("--participants", required=True, help="Comma-separated participant names")
    ingest_parser.add_argument("--org", help="Organization name")
    ingest_parser.add_argument("--summary", help="Brief summary")
    ingest_parser.add_argument("--source-type", default="call_transcript", choices=["call_transcript", "meeting_notes"])

    args = parser.parse_args()

    if args.command == "ingest":
        ingest(
            file_path=args.file,
            stakeholder_name=args.stakeholder,
            stakeholder_type=args.type,
            call_date=date.fromisoformat(args.date),
            participants=[p.strip() for p in args.participants.split(",")],
            organization=args.org,
            summary=args.summary,
            source_type=args.source_type,
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
