#!/usr/bin/env python3
"""
Ingest a reference document into the knowledge base.

For framework docs, methodologies, and other reference materials.
"""

import argparse
from pathlib import Path
from kb_core import chunk_by_sections, get_embedding, get_db


def ingest_reference_doc(file_path: str, category: str, title: str) -> dict:
    """Ingest a reference document into reference_docs table."""

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    # Read content
    content = path.read_text()

    # Chunk using section-based chunking (better for structured docs)
    chunks = chunk_by_sections(content, min_chunk_size=100)

    if not chunks:
        return {"error": "No chunks generated"}

    print(f"Generated {len(chunks)} chunks")

    with get_db() as conn:
        # Check if doc already exists
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM reference_docs WHERE title = %s AND category = %s",
                (title, category)
            )
            existing = cur.fetchone()

            if existing:
                # Update existing
                doc_id = existing["id"]
                cur.execute(
                    "UPDATE reference_docs SET content = %s, source_file = %s WHERE id = %s",
                    (content, str(path), doc_id)
                )
                print(f"Updated reference doc ID: {doc_id}")
            else:
                # Insert new
                cur.execute(
                    """INSERT INTO reference_docs (title, category, content, source_file)
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (title, category, content, str(path))
                )
                doc_id = cur.fetchone()["id"]
                print(f"Created reference doc ID: {doc_id}")

        # Delete old chunks for this doc (if re-ingesting)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reference_doc_chunks WHERE doc_id = %s", (doc_id,))

        # Embed and insert chunks
        for idx, chunk_text in enumerate(chunks):
            embedding = get_embedding(chunk_text)
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO reference_doc_chunks (doc_id, chunk_idx, text, embedding)
                       VALUES (%s, %s, %s, %s)""",
                    (doc_id, idx, chunk_text, embedding)
                )

            if (idx + 1) % 10 == 0:
                print(f"  Embedded {idx + 1}/{len(chunks)} chunks...")

        conn.commit()

    return {
        "doc_id": doc_id,
        "title": title,
        "category": category,
        "chunks_indexed": len(chunks),
        "file": str(path)
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest Reference Document")
    parser.add_argument("file", help="Path to document")
    parser.add_argument("--title", required=True, help="Document title")
    parser.add_argument("--category", required=True,
                       choices=["sales_framework", "product_docs", "internal_process", "other"],
                       help="Document category")

    args = parser.parse_args()

    result = ingest_reference_doc(args.file, args.category, args.title)

    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        print(f"\n✓ Successfully ingested: {result['title']}")
        print(f"  Category: {result['category']}")
        print(f"  Chunks: {result['chunks_indexed']}")
        print(f"  Doc ID: {result['doc_id']}")
