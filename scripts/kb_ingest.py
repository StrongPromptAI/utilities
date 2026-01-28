#!/usr/bin/env python3
"""
Knowledge Base Ingestion Script

Chunks documents and stores in Postgres with pgvector embeddings.
Uses LM Studio for local embeddings.
"""

import argparse
import psycopg
from psycopg.rows import dict_row
from openai import OpenAI
from pathlib import Path
from datetime import date


# Config
DB_URL = "postgresql://postgres:55@localhost/knowledge_base"
LM_STUDIO_URL = "http://localhost:1234/v1"
EMBED_MODEL = "nomic-embed-text"


def get_embedding(client: OpenAI, text: str) -> list[float]:
    """Generate embedding via LM Studio."""
    response = client.embeddings.create(model=EMBED_MODEL, input=text)
    return response.data[0].embedding


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    """Fixed-size chunking with overlap."""
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def ensure_stakeholder(conn, name: str, type: str, organization: str = None) -> int:
    """Get or create stakeholder, return ID."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM stakeholders WHERE name = %s",
            (name,)
        )
        row = cur.fetchone()
        if row:
            return row["id"]

        cur.execute(
            """INSERT INTO stakeholders (name, type, organization)
               VALUES (%s, %s, %s) RETURNING id""",
            (name, type, organization)
        )
        return cur.fetchone()["id"]


def ingest_call(
    file_path: str,
    stakeholder_name: str,
    stakeholder_type: str,
    call_date: date,
    participants: list[str],
    source_type: str = "call_transcript",
    organization: str = None,
    summary: str = None,
    chunk_size: int = 512,
    overlap: int = 50
) -> dict:
    """Ingest a document into the knowledge base."""

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    text = path.read_text()
    chunks = chunk_text(text, chunk_size, overlap)

    if not chunks:
        return {"error": "No chunks generated"}

    # LM Studio client
    lm = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")

    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        # Ensure stakeholder exists
        stakeholder_id = ensure_stakeholder(conn, stakeholder_name, stakeholder_type, organization)

        # Insert call
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO calls (call_date, participants, stakeholder_id, source_type, source_file, summary)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (call_date, participants, stakeholder_id, source_type, str(path), summary)
            )
            call_id = cur.fetchone()["id"]

        # Chunk, embed, insert
        for idx, chunk in enumerate(chunks):
            embedding = get_embedding(lm, chunk)
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO chunks (call_id, chunk_idx, text, embedding)
                       VALUES (%s, %s, %s, %s)""",
                    (call_id, idx, chunk, embedding)
                )

            if (idx + 1) % 20 == 0:
                print(f"  Embedded {idx + 1}/{len(chunks)} chunks...")

        conn.commit()

    return {
        "stakeholder": stakeholder_name,
        "call_id": call_id,
        "chunks_indexed": len(chunks),
        "file": str(path)
    }


def search(query: str, stakeholder: str = None, limit: int = 5) -> list[dict]:
    """Semantic search with optional stakeholder filter."""
    lm = OpenAI(base_url=LM_STUDIO_URL, api_key="not-needed")
    query_embedding = get_embedding(lm, query)

    with psycopg.connect(DB_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if stakeholder:
                cur.execute(
                    """SELECT text, stakeholder_name, call_date,
                              embedding <=> %s::vector AS distance
                       FROM chunks_with_context
                       WHERE stakeholder_name = %s
                       ORDER BY embedding <=> %s::vector
                       LIMIT %s""",
                    (query_embedding, stakeholder, query_embedding, limit)
                )
            else:
                cur.execute(
                    """SELECT text, stakeholder_name, call_date,
                              embedding <=> %s::vector AS distance
                       FROM chunks_with_context
                       ORDER BY embedding <=> %s::vector
                       LIMIT %s""",
                    (query_embedding, query_embedding, limit)
                )
            return cur.fetchall()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Knowledge Base Ingestion")
    subparsers = parser.add_subparsers(dest="command")

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a document")
    ingest_parser.add_argument("file", help="Path to document")
    ingest_parser.add_argument("--stakeholder", required=True, help="Stakeholder name")
    ingest_parser.add_argument("--type", required=True, choices=["doctor", "partner", "vendor", "investor", "employee", "other"])
    ingest_parser.add_argument("--date", required=True, help="Call date (YYYY-MM-DD)")
    ingest_parser.add_argument("--participants", required=True, help="Comma-separated participant names")
    ingest_parser.add_argument("--org", help="Organization name")
    ingest_parser.add_argument("--summary", help="Brief summary")

    # Search command
    search_parser = subparsers.add_parser("search", help="Search the knowledge base")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--stakeholder", help="Filter by stakeholder name")
    search_parser.add_argument("--limit", type=int, default=5)

    args = parser.parse_args()

    if args.command == "ingest":
        result = ingest_call(
            file_path=args.file,
            stakeholder_name=args.stakeholder,
            stakeholder_type=args.type,
            call_date=date.fromisoformat(args.date),
            participants=[p.strip() for p in args.participants.split(",")],
            organization=args.org,
            summary=args.summary
        )
        print(result)

    elif args.command == "search":
        results = search(args.query, args.stakeholder, args.limit)
        for r in results:
            print(f"\n[{r['stakeholder_name']} - {r['call_date']}] (dist: {r['distance']:.3f})")
            print(f"  {r['text'][:200]}...")

    else:
        parser.print_help()
