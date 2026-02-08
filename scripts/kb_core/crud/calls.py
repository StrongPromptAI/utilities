"""Call CRUD operations."""

from typing import Optional
from datetime import date
from ..db import get_db
from .contacts import get_call_contacts
from .chunks import get_call_chunks, get_call_batch_summaries


def get_call_by_source_file(source_file: str) -> Optional[dict]:
    """Check if a call with this source file already exists.

    Returns the call record if found, None otherwise.
    Use to prevent duplicate ingestion.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*, o.name as org_name, p.name as project_name,
                          (SELECT count(*) FROM call_chunks WHERE call_id = c.id) as chunk_count
                   FROM calls c
                   JOIN orgs o ON c.org_id = o.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.source_file = %s""",
                (source_file,)
            )
            return cur.fetchone()


def delete_call(call_id: int) -> dict:
    """Delete a call and all its chunks (for re-ingestion).

    Returns info about what was deleted.
    Chunks are deleted automatically via ON DELETE CASCADE.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            # Get info before deletion
            cur.execute(
                """SELECT c.id, c.call_date, c.source_file, o.name as org_name,
                          (SELECT count(*) FROM call_chunks WHERE call_id = c.id) as chunk_count
                   FROM calls c
                   JOIN orgs o ON c.org_id = o.id
                   WHERE c.id = %s""",
                (call_id,)
            )
            call_info = cur.fetchone()
            if not call_info:
                return {"error": f"Call {call_id} not found"}

            # Delete (chunks cascade)
            cur.execute("DELETE FROM calls WHERE id = %s", (call_id,))
            conn.commit()

            return {
                "deleted_call_id": call_info["id"],
                "call_date": call_info["call_date"],
                "org": call_info["org_name"],
                "chunks_deleted": call_info["chunk_count"],
                "source_file": call_info["source_file"]
            }


def create_call(
    call_date: date,
    org_id: int,
    source_type: str,
    source_file: str = None,
    summary: str = None,
    project_id: int = None,
    user_notes: str = None
) -> int:
    """Create a call record, return ID.

    user_notes: Optional personal observations/thoughts about the call.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO calls (call_date, org_id, source_type, source_file, summary, project_id, user_notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (call_date, org_id, source_type, source_file, summary, project_id, user_notes)
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_calls_for_org(org_name: str) -> list[dict]:
    """Get all calls for an org."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*, o.name as org_name, p.name as project_name
                   FROM calls c
                   JOIN orgs o ON c.org_id = o.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE o.name = %s
                   ORDER BY c.call_date DESC""",
                (org_name,)
            )
            return cur.fetchall()


def update_call_summary(call_id: int, summary: str) -> bool:
    """Update the summary field for a call (after HITL review)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE calls SET summary = %s WHERE id = %s",
                (summary, call_id)
            )
            conn.commit()
            return cur.rowcount > 0


def update_user_notes(call_id: int, notes: str) -> bool:
    """Update user_notes for a call."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE calls SET user_notes = %s WHERE id = %s",
                (notes, call_id)
            )
            conn.commit()
            return cur.rowcount > 0


def list_calls(project_id: int = None, limit: int = 20) -> list[dict]:
    """List calls with org and project names.

    Args:
        project_id: Filter by project, or None for all.
        limit: Max results (default 20).
    """
    query = """
        SELECT c.id, c.call_date, c.source_type, c.summary, c.user_notes,
               o.name as org_name, p.name as project_name
        FROM calls c
        JOIN orgs o ON c.org_id = o.id
        LEFT JOIN projects p ON c.project_id = p.id
    """
    params: list = []
    if project_id:
        query += " WHERE c.project_id = %s"
        params.append(project_id)
    query += " ORDER BY c.call_date DESC LIMIT %s"
    params.append(limit)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def get_call_detail(call_id: int) -> Optional[dict]:
    """Get full call detail: call + contacts + summaries + chunks.

    Returns None if call not found.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*, o.name as org_name, p.name as project_name
                   FROM calls c
                   JOIN orgs o ON c.org_id = o.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE c.id = %s""",
                (call_id,),
            )
            call = cur.fetchone()
            if not call:
                return None

    contacts = get_call_contacts(call_id)
    summaries = get_call_batch_summaries(call_id)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, chunk_idx, text, speaker FROM call_chunks
                   WHERE call_id = %s ORDER BY chunk_idx""",
                (call_id,),
            )
            chunks = cur.fetchall()

    return {
        "call": call,
        "contacts": contacts,
        "summaries": summaries,
        "chunks": chunks,
    }
