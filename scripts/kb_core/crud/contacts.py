"""Contact CRUD operations."""

from typing import Optional
from ..db import get_db


def get_contact(name: str) -> Optional[dict]:
    """Get contact by name (case-insensitive partial match)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM contacts WHERE name ILIKE %s ORDER BY name LIMIT 1",
                (f"%{name}%",),
            )
            return cur.fetchone()


def get_contact_by_id(contact_id: int) -> Optional[dict]:
    """Get contact by ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM contacts WHERE id = %s", (contact_id,))
            return cur.fetchone()


def list_contacts(org_id: int = None) -> list[dict]:
    """List all contacts, optionally filtered by org."""
    with get_db() as conn:
        with conn.cursor() as cur:
            if org_id:
                cur.execute(
                    """SELECT c.*, o.name as org_name
                       FROM contacts c
                       LEFT JOIN orgs o ON c.org_id = o.id
                       WHERE c.org_id = %s ORDER BY c.name""",
                    (org_id,),
                )
            else:
                cur.execute(
                    """SELECT c.*, o.name as org_name
                       FROM contacts c
                       LEFT JOIN orgs o ON c.org_id = o.id
                       ORDER BY c.name"""
                )
            return cur.fetchall()


def create_contact(
    name: str,
    org_id: int = None,
    role: str = None,
    email: str = None,
    notes: str = None,
) -> int:
    """Create contact, return ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO contacts (name, org_id, role, email, notes)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (name, org_id, role, email, notes),
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_or_create_contact(name: str, org_id: int = None) -> int:
    """Get existing contact ID or create new one."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                return row["id"]
            cur.execute(
                "INSERT INTO contacts (name, org_id) VALUES (%s, %s) RETURNING id",
                (name, org_id),
            )
            conn.commit()
            return cur.fetchone()["id"]


def add_contacts_to_call(call_id: int, contact_ids: list[int]) -> int:
    """Link contacts to a call via call_contacts junction. Returns count added."""
    count = 0
    with get_db() as conn:
        with conn.cursor() as cur:
            for cid in contact_ids:
                cur.execute(
                    """INSERT INTO call_contacts (call_id, contact_id)
                       VALUES (%s, %s)
                       ON CONFLICT (call_id, contact_id) DO NOTHING""",
                    (call_id, cid),
                )
                count += cur.rowcount
            conn.commit()
    return count


def get_call_contacts(call_id: int) -> list[dict]:
    """Get all contacts for a call."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.*, o.name as org_name
                   FROM contacts c
                   JOIN call_contacts cc ON cc.contact_id = c.id
                   LEFT JOIN orgs o ON c.org_id = o.id
                   WHERE cc.call_id = %s
                   ORDER BY c.name""",
                (call_id,),
            )
            return cur.fetchall()


def get_calls_by_contact(name: str) -> list[dict]:
    """Get all calls where a person participated."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT ca.*, o.name as org_name, p.name as project_name
                   FROM calls ca
                   JOIN call_contacts cc ON cc.call_id = ca.id
                   JOIN contacts ct ON cc.contact_id = ct.id
                   JOIN orgs o ON ca.org_id = o.id
                   LEFT JOIN projects p ON ca.project_id = p.id
                   WHERE ct.name ILIKE %s
                   ORDER BY ca.call_date DESC""",
                (f"%{name}%",),
            )
            return cur.fetchall()
