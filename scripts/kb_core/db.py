"""Database connection management."""

import psycopg
from psycopg.rows import dict_row
from .config import DB_URL


def get_db():
    """Get database connection."""
    return psycopg.connect(DB_URL, row_factory=dict_row)
