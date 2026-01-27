#!/usr/bin/env python3
"""
Ingest a reference document with preprocessing to remove bibliography/references.
Uses fixed-size chunking for consistency.
"""

import argparse
import re
from pathlib import Path
from kb_core import chunk_text as create_chunks, get_embedding, get_db


def strip_references_section(content: str) -> str:
    """Remove References/Bibliography section from end of document."""
    # Look for common reference section headers
    patterns = [
        r'\n\s*References\s*\n',
        r'\n\s*Bibliography\s*\n',
        r'\n\s*Works Cited\s*\n',
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            # Cut content at the start of references section
            content = content[:match.start()]
            print(f"✓ Stripped references section (removed {len(content[match.start():])} chars)")
            break

    return content


def ingest_reference_doc(file_path: str, category: str, title: str,
                         chunk_size: int = 1000, overlap: int = 100) -> dict:
    """Ingest a reference document with fixed-size chunking."""

    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    # Read content
    content = path.read_text()

    # Strip references
    content = strip_references_section(content)

    # Fixed-size chunking (more reliable for these docs)
    chunks = create_chunks(content, chunk_size=chunk_size, overlap=overlap)

    if not chunks:
        return {"error": "No chunks generated"}

    print(f"Generated {len(chunks)} chunks ({chunk_size} chars each, {overlap} overlap)")

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

            if (idx + 1) % 20 == 0:
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
    parser = argparse.ArgumentParser(description="Ingest Reference Document (Clean)")
    parser.add_argument("file", help="Path to document")
    parser.add_argument("--title", required=True, help="Document title")
    parser.add_argument("--category", required=True,
                       choices=["sales_framework", "product_docs", "internal_process", "other"],
                       help="Document category")
    parser.add_argument("--chunk-size", type=int, default=1000, help="Chunk size in chars")
    parser.add_argument("--overlap", type=int, default=100, help="Overlap between chunks")

    args = parser.parse_args()

    result = ingest_reference_doc(args.file, args.category, args.title,
                                  args.chunk_size, args.overlap)

    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        print(f"\n✓ Successfully ingested: {result['title']}")
        print(f"  Category: {result['category']}")
        print(f"  Chunks: {result['chunks_indexed']}")
        print(f"  Doc ID: {result['doc_id']}")
