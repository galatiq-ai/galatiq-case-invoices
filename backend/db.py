"""SQLite access. Init is lazy (not a lifespan hook): the CLI mounts the app via
ASGITransport, which doesn't run lifespan events."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS greetings (
    id      INTEGER PRIMARY KEY,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wide_events (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    type        TEXT NOT NULL,
    level       TEXT NOT NULL,
    source      TEXT NOT NULL,
    path        TEXT,
    method      TEXT,
    status_code INTEGER,
    duration_ms REAL,
    error       INTEGER NOT NULL DEFAULT 0,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wide_events_trace ON wide_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_wide_events_created ON wide_events(created_at DESC);
"""

_initialized = False


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO greetings (id, message) VALUES (1, 'hello world')")
        conn.commit()
    finally:
        conn.close()
    _initialized = True


def connect() -> sqlite3.Connection:
    _ensure_init()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
