"""SQLite engine on the Railway volume.

The DB lives at `PODCAST_DB_PATH` (default `/data/podcast.db` — on the mounted
Railway volume in prod; override to `./data/podcast.db` for local dev). WAL mode
with `synchronous=NORMAL` is the right tuning for a single-writer service on
block storage: durable enough, no fsync-per-write latency spikes. SINGLE REPLICA
ONLY — WAL has one writer, so the service must never scale past one instance.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from models import Base

DB_PATH = os.environ.get("PODCAST_DB_PATH", "/data/podcast.db")

# Ensure the parent dir exists (the volume mount, or ./data locally).
Path(DB_PATH).expanduser().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{Path(DB_PATH).expanduser()}",
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA wal_autocheckpoint=1000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


SessionLocal = sessionmaker(bind=engine, class_=Session, future=True, expire_on_commit=False)


def init_db() -> None:
    """Create tables if absent + apply lightweight column migrations. Idempotent; safe on every boot."""
    Base.metadata.create_all(engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """Add columns that `create_all` won't add to a pre-existing table — SQLite's
    `ALTER TABLE ADD COLUMN` is the one safe in-place migration. Guarded on `PRAGMA table_info`
    so it's idempotent (a fresh DB already has the column from `create_all`; an existing one gets
    it added once). Fail-fast: a genuine ALTER error surfaces at boot, not mid-request."""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(episodes)")}
        if "updated_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE episodes ADD COLUMN updated_at DATETIME")
