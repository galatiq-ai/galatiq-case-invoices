"""SQLite access. Init is lazy (not a lifespan hook): the CLI mounts the app via
ASGITransport, which doesn't run lifespan events. Schema and reference data live
in schema.sql / seed.sql; both are applied idempotently on first connect."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "app.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
SEED_PATH = Path(__file__).resolve().parent / "seed.sql"

_initialized = False


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_PATH.read_text())
        conn.executescript(SEED_PATH.read_text())
        conn.commit()
    finally:
        conn.close()
    _initialized = True


def connect() -> sqlite3.Connection:
    _ensure_init()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
