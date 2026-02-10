"""Action items CRUD operations."""

from typing import Optional
from ..db import get_db


def create_action(
    project_id: int,
    title: str,
    description: str = None,
    assigned_contact_id: int = None,
    source_call_id: int = None,
    question_id: int = None,
) -> int:
    """Insert an action item. Returns ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO action_items
                   (project_id, title, description, assigned_contact_id,
                    source_call_id, question_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (project_id, title, description, assigned_contact_id,
                 source_call_id, question_id),
            )
            conn.commit()
            return cur.fetchone()["id"]


def list_actions(project_id: int, status: str = None) -> list[dict]:
    """List action items for a project, optionally filtered by status."""
    query = """
        SELECT a.*, q.topic as question_topic, q.status as question_status,
               c.name as assigned_name
        FROM action_items a
        LEFT JOIN questions q ON a.question_id = q.id
        LEFT JOIN contacts c ON a.assigned_contact_id = c.id
        WHERE a.project_id = %s
    """
    params: list = [project_id]
    if status:
        query += " AND a.status = %s"
        params.append(status)
    query += " ORDER BY a.status = 'done', a.created_at DESC"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def get_action(action_id: int) -> Optional[dict]:
    """Get a single action item by ID with question and contact context."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT a.*, q.topic as question_topic, q.status as question_status,
                          c.name as assigned_name
                   FROM action_items a
                   LEFT JOIN questions q ON a.question_id = q.id
                   LEFT JOIN contacts c ON a.assigned_contact_id = c.id
                   WHERE a.id = %s""",
                (action_id,),
            )
            return cur.fetchone()


def get_action_prompt_file(action_id: int) -> Optional[str]:
    """Get the prompt_file field for an action item. Returns None if not set."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT prompt_file FROM action_items WHERE id = %s",
                (action_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return row.get("prompt_file")


def update_action_status(action_id: int, status: str) -> bool:
    """Update action item status."""
    with get_db() as conn:
        with conn.cursor() as cur:
            if status == "done":
                cur.execute(
                    "UPDATE action_items SET status = %s, completed_at = now() WHERE id = %s",
                    (status, action_id),
                )
            else:
                cur.execute(
                    "UPDATE action_items SET status = %s WHERE id = %s",
                    (status, action_id),
                )
            conn.commit()
            return cur.rowcount > 0


def clear_candidate_actions(call_id: int) -> int:
    """Delete open action items sourced from a specific call (for re-harvest)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM action_items
                   WHERE status = 'open' AND source_call_id = %s""",
                (call_id,),
            )
            conn.commit()
            return cur.rowcount


def insert_candidate_actions(
    project_id: int, call_id: int, actions: list[dict]
) -> int:
    """Bulk insert candidate action items from harvest. Returns count.

    Each action dict should have: title, description, assigned_contact_id.
    """
    if not actions:
        return 0

    with get_db() as conn:
        with conn.cursor() as cur:
            for a in actions:
                cur.execute(
                    """INSERT INTO action_items
                       (project_id, title, description, assigned_contact_id,
                        source_call_id, status, stakeholder_type)
                       VALUES (%s, %s, %s, %s, %s, 'open', %s)""",
                    (
                        project_id,
                        a["title"],
                        a.get("description"),
                        a.get("assigned_contact_id"),
                        call_id,
                        a.get("stakeholder_type"),
                    ),
                )
        conn.commit()
    return len(actions)


def get_candidate_actions(project_id: int, call_id: int = None) -> list[dict]:
    """Get open (candidate) action items, optionally filtered by source call."""
    query = """SELECT a.*, c.name as assigned_name
               FROM action_items a
               LEFT JOIN contacts c ON a.assigned_contact_id = c.id
               WHERE a.project_id = %s AND a.status = 'open'"""
    params: list = [project_id]
    if call_id:
        query += " AND a.source_call_id = %s"
        params.append(call_id)
    query += " ORDER BY a.id"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def confirm_action(action_id: int) -> bool:
    """Confirm an action item (keep as open task)."""
    # Actions stay 'open' when approved — they're real tasks now
    return True


def reject_action(action_id: int) -> bool:
    """Delete a rejected candidate action item."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM action_items WHERE id = %s AND status = 'open'",
                (action_id,),
            )
            conn.commit()
            return cur.rowcount > 0
