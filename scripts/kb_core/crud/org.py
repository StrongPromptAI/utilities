"""Org CRUD operations."""

from typing import Optional
from ..db import get_db


def get_org(name: str) -> Optional[dict]:
    """Get org by name."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM orgs WHERE name = %s", (name,))
            return cur.fetchone()


def list_org(type_filter: str = None) -> list[dict]:
    """List all orgs, optionally filtered by type."""
    with get_db() as conn:
        with conn.cursor() as cur:
            if type_filter:
                cur.execute("SELECT * FROM orgs WHERE type = %s ORDER BY name", (type_filter,))
            else:
                cur.execute("SELECT * FROM orgs ORDER BY name")
            return cur.fetchall()


def create_org(name: str, type: str, notes: str = None) -> int:
    """Create org, return ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO orgs(name, type, notes)
                   VALUES (%s, %s, %s) RETURNING id""",
                (name, type, notes)
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_or_create_org(name: str, type: str) -> int:
    """Get existing org ID or create new one."""
    existing = get_org(name)
    if existing:
        return existing["id"]
    return create_org(name, type)
