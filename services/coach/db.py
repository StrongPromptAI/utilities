"""kb Postgres connection for the coach service.

COACH_DB_URL is the kb Postgres — over `railway.internal` in prod (no egress), the
public proxy locally. Fail-fast: no silent default.
"""
from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row


def get_conn():
    url = os.environ.get("COACH_DB_URL")
    if not url:
        raise RuntimeError("FAIL-FAST: COACH_DB_URL not set")
    return psycopg.connect(url, row_factory=dict_row)
