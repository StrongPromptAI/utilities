"""Call CRUD operations."""

from typing import Optional
from datetime import date
from ..db import get_db


def get_call_by_source_file(source_file: str) -> Optional[dict]:
    """Check if a call with this source file already exists.

    Returns the call record if found, None otherwise.
    Use to prevent duplicate ingestion.
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*, s.name as client_name, p.name as project_name,
                          (SELECT count(*) FROM chunks WHERE call_id = c.id) as chunk_count
                   FROM calls c
                   JOIN clients s ON c.client_id = s.id
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
                """SELECT c.id, c.call_date, c.source_file, s.name as client_name,
                          (SELECT count(*) FROM chunks WHERE call_id = c.id) as chunk_count
                   FROM calls c
                   JOIN clients s ON c.client_id = s.id
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
                "client": call_info["client_name"],
                "chunks_deleted": call_info["chunk_count"],
                "source_file": call_info["source_file"]
            }


def create_call(
    call_date: date,
    client_id: int,
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
                """INSERT INTO calls (call_date, client_id, source_type, source_file, summary, project_id, user_notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (call_date, client_id, source_type, source_file, summary, project_id, user_notes)
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_calls_for_client(client_name: str) -> list[dict]:
    """Get all calls for a client."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*, s.name as client_name, p.name as project_name
                   FROM calls c
                   JOIN clients s ON c.client_id = s.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE s.name = %s
                   ORDER BY c.call_date DESC""",
                (client_name,)
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
