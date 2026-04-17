"""CRUD operations for ingest_sources and ingest_chunks."""

from typing import Optional
from datetime import date
from ..db import get_db
from ..embeddings import get_embedding


def create_ingest_source(
    org_id: int,
    source_type: str,
    source_file: str,
    raw_text: str,
    project_id: int = None,
    source_date: date = None,
    agent_name: str = None,
    segment_count: int = None,
) -> int:
    """Create an ingest source record, return ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ingest_sources
                   (org_id, project_id, source_type, source_file, source_date,
                    agent_name, raw_text, segment_count)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (org_id, project_id, source_type, source_file, source_date,
                 agent_name, raw_text, segment_count),
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_ingest_source(source_id: int) -> Optional[dict]:
    """Get a single ingest source by ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.*, o.name as org_name, p.name as project_name
                   FROM ingest_sources s
                   JOIN orgs o ON s.org_id = o.id
                   LEFT JOIN projects p ON s.project_id = p.id
                   WHERE s.id = %s""",
                (source_id,),
            )
            return cur.fetchone()


def get_ingest_source_by_file(source_file: str) -> Optional[dict]:
    """Check if a source with this file already exists (dedup)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, source_file FROM ingest_sources WHERE source_file = %s",
                (source_file,),
            )
            return cur.fetchone()


def list_ingest_sources(
    project_id: int = None,
    source_type: str = None,
    scope: str = None,
    category: str = None,
    limit: int = 100,
) -> list[dict]:
    """List ingest sources with filters.

    Args:
        scope: 'in', 'out', or 'unclassified'
    """
    query = """
        SELECT s.id, s.source_type, s.source_file, s.source_date, s.agent_name,
               s.category, s.in_scope, s.segment_count, s.raw_text,
               o.name as org_name, p.name as project_name
        FROM ingest_sources s
        JOIN orgs o ON s.org_id = o.id
        LEFT JOIN projects p ON s.project_id = p.id
        WHERE 1=1
    """
    params: list = []

    if project_id:
        query += " AND s.project_id = %s"
        params.append(project_id)
    if source_type:
        query += " AND s.source_type = %s"
        params.append(source_type)
    if scope == "in":
        query += " AND s.in_scope = true"
    elif scope == "out":
        query += " AND s.in_scope = false"
    elif scope == "unclassified":
        query += " AND s.category IS NULL"
    if category:
        query += " AND s.category = %s"
        params.append(category)

    query += " ORDER BY s.source_date DESC NULLS LAST, s.id DESC LIMIT %s"
    params.append(limit)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def update_classification(
    source_id: int, category: str, in_scope: bool, notes: str = None
) -> bool:
    """Set classification on an ingest source."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE ingest_sources
                   SET category = %s, in_scope = %s, classification_notes = %s
                   WHERE id = %s""",
                (category, in_scope, notes, source_id),
            )
            conn.commit()
            return cur.rowcount > 0


def ingest_stats(project_id: int = None, source_type: str = None) -> dict:
    """Get category breakdown counts."""
    where = "WHERE 1=1"
    params: list = []
    if project_id:
        where += " AND project_id = %s"
        params.append(project_id)
    if source_type:
        where += " AND source_type = %s"
        params.append(source_type)

    with get_db() as conn:
        with conn.cursor() as cur:
            # Category breakdown
            cur.execute(
                f"""SELECT category, in_scope, count(*) as cnt
                    FROM ingest_sources {where}
                    GROUP BY category, in_scope
                    ORDER BY cnt DESC""",
                params,
            )
            rows = cur.fetchall()

            # Totals
            cur.execute(
                f"SELECT count(*) as total FROM ingest_sources {where}", params
            )
            total = cur.fetchone()["total"]

            # By source type
            cur.execute(
                f"""SELECT source_type, count(*) as cnt
                    FROM ingest_sources {where}
                    GROUP BY source_type ORDER BY cnt DESC""",
                params,
            )
            by_type = cur.fetchall()

    return {"categories": rows, "total": total, "by_type": by_type}


def insert_ingest_chunks(
    source_id: int, chunks: list[dict], show_progress: bool = True
) -> int:
    """Embed and insert chunks for an ingest source. Returns count.

    Args:
        chunks: List of {"text": str, "timestamp_start": str, "timestamp_end": str}
    """
    with get_db() as conn:
        for idx, chunk in enumerate(chunks):
            embedding = get_embedding(chunk["text"])
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ingest_chunks
                       (ingest_source_id, chunk_idx, text, timestamp_start, timestamp_end, embedding)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (source_id, idx, chunk["text"],
                     chunk.get("timestamp_start"), chunk.get("timestamp_end"),
                     embedding),
                )
            if show_progress and (idx + 1) % 20 == 0:
                print(f"  Embedded {idx + 1}/{len(chunks)} chunks...")
        conn.commit()
    return len(chunks)
