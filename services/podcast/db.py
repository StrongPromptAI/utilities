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
    """Create tables if absent. Idempotent; safe on every boot."""
    Base.metadata.create_all(engine)
