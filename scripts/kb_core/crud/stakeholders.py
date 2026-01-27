"""Stakeholder CRUD operations."""

from typing import Optional
from ..db import get_db


def get_stakeholder(name: str) -> Optional[dict]:
    """Get stakeholder by name."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM stakeholders WHERE name = %s", (name,))
            return cur.fetchone()


def list_stakeholders(type_filter: str = None) -> list[dict]:
    """List all stakeholders, optionally filtered by type."""
    with get_db() as conn:
        with conn.cursor() as cur:
            if type_filter:
                cur.execute("SELECT * FROM stakeholders WHERE type = %s ORDER BY name", (type_filter,))
            else:
                cur.execute("SELECT * FROM stakeholders ORDER BY name")
            return cur.fetchall()


def create_stakeholder(name: str, type: str, organization: str = None, notes: str = None) -> int:
    """Create stakeholder, return ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO stakeholders (name, type, organization, notes)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (name, type, organization, notes)
            )
            conn.commit()
            return cur.fetchone()["id"]


def get_or_create_stakeholder(name: str, type: str, organization: str = None) -> int:
    """Get existing stakeholder ID or create new one."""
    existing = get_stakeholder(name)
    if existing:
        return existing["id"]
    return create_stakeholder(name, type, organization)
