"""Participant CRUD operations."""

from ..db import get_db


def add_participant(call_id: int, name: str, role: str = None) -> int:
    """Add a participant to a call, return participant ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO participants (call_id, name, role)
                   VALUES (%s, %s, %s) RETURNING id""",
                (call_id, name, role)
            )
            conn.commit()
            return cur.fetchone()["id"]


def add_participants(call_id: int, names: list[str]) -> list[int]:
    """Add multiple participants to a call, return list of IDs."""
    ids = []
    with get_db() as conn:
        with conn.cursor() as cur:
            for name in names:
                cur.execute(
                    """INSERT INTO participants (call_id, name)
                       VALUES (%s, %s) RETURNING id""",
                    (call_id, name)
                )
                ids.append(cur.fetchone()["id"])
            conn.commit()
    return ids


def get_call_participants(call_id: int) -> list[dict]:
    """Get all participants for a call."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM participants WHERE call_id = %s ORDER BY name",
                (call_id,)
            )
            return cur.fetchall()


def get_calls_by_participant(name: str) -> list[dict]:
    """Get all calls where a person participated."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT c.*, cl.name as client_name, p.name as project_name
                   FROM calls c
                   JOIN participants pt ON pt.call_id = c.id
                   JOIN clients cl ON c.client_id = cl.id
                   LEFT JOIN projects p ON c.project_id = p.id
                   WHERE pt.name ILIKE %s
                   ORDER BY c.call_date DESC""",
                (f"%{name}%",)
            )
            return cur.fetchall()
