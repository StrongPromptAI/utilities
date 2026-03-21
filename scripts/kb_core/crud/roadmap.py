"""Roadmap items CRUD operations."""

from typing import Optional
from ..db import get_db


def create_roadmap_item(
    project_id: int,
    title: str,
    spoke: str,
    round: int,
    description: str = None,
    status: str = "planned",
) -> int:
    """Insert a roadmap item. Returns ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO roadmap_items
                   (project_id, title, description, spoke, round, status)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (project_id, title, description, spoke, round, status),
            )
            conn.commit()
            return cur.fetchone()["id"]


def list_roadmap_items(
    project_id: int,
    spoke: str = None,
    round: int = None,
    status: str = None,
) -> list[dict]:
    """List roadmap items for a project with optional filters."""
    query = "SELECT * FROM roadmap_items WHERE project_id = %s"
    params: list = [project_id]
    if spoke:
        query += " AND spoke = %s"
        params.append(spoke)
    if round is not None:
        query += " AND round = %s"
        params.append(round)
    if status:
        query += " AND status = %s"
        params.append(status)
    query += " ORDER BY round, status = 'done', created_at"

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def get_roadmap_item(item_id: int) -> Optional[dict]:
    """Get a single roadmap item by ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM roadmap_items WHERE id = %s", (item_id,))
            return cur.fetchone()


def update_roadmap_item(item_id: int, **fields) -> bool:
    """Update any combination of title, description, spoke, round, status."""
    allowed = {"title", "description", "spoke", "round", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False

    sets = [f"{k} = %s" for k in updates]
    sets.append("updated_at = now()")
    vals = list(updates.values())
    vals.append(item_id)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE roadmap_items SET {', '.join(sets)} WHERE id = %s",
                vals,
            )
            conn.commit()
            return cur.rowcount > 0


def delete_roadmap_item(item_id: int) -> bool:
    """Delete a roadmap item."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM roadmap_items WHERE id = %s", (item_id,))
            conn.commit()
            return cur.rowcount > 0
