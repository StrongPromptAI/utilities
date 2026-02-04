"""Client CRUD operations."""

from typing import Optional
from ..db import get_db


def get_client(name: str) -> Optional[dict]:
    """Get client by name."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM clients WHERE name = %s", (name,))
            return cur.fetchone()


def list_clients(type_filter: str = None) -> list[dict]:
    """List all clients, optionally filtered by type."""
    with get_db() as conn:
        with conn.cursor() as cur:
            if type_filter:
                cur.execute("SELECT * FROM clients WHERE type = %s ORDER BY name", (type_filter,))
            else:
                cur.execute("SELECT * FROM clients ORDER BY name")
            return cur.fetchall()


def create_client(name: str, type: str, organization: str = None, notes: str = None) -> int:
    """Create client, return ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO clients (name, type, organization, notes)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (name, type, organization, notes)
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_or_create_client(name: str, type: str, organization: str = None) -> int:
    """Get existing client ID or create new one."""
    existing = get_client(name)
    if existing:
        return existing["id"]
    return create_client(name, type, organization)
