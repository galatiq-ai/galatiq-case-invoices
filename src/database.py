"""Database layer — SQLite initialization, seeding, and query functions."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path("storage/inventory.db")


# Central audit logs DB
LOGS_DB_PATH = Path("logs/agent_logs.db")


def get_logs_connection() -> sqlite3.Connection:
    LOGS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(LOGS_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_logs_db() -> None:
    conn = get_logs_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            invoice_number TEXT,
            agent TEXT,
            status TEXT,
            decision TEXT,
            flags TEXT,
            total REAL,
            vendor TEXT
        )
    """)
    # Ensure the newer audit tables exist as a migration path.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            invoice_number TEXT,
            file_name TEXT,
            stage TEXT,
            status TEXT,
            decision TEXT,
            reason TEXT,
            flags TEXT,
            actor TEXT,
            metadata TEXT,
            total REAL,
            vendor TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoice_summaries (
            invoice_number TEXT PRIMARY KEY,
            first_seen TEXT,
            last_seen TEXT,
            status TEXT,
            total REAL,
            vendor TEXT,
            events_count INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def log_agent_event(
    invoice_number: str, agent: str, status: str,
    decision: str = "", flags: str = "",
    total: float = 0.0, vendor: str = "",
) -> None:
    """Write one audit row to logs/agent_logs.db."""
    init_logs_db()
    conn = get_logs_connection()
    conn.execute(
        """INSERT INTO agent_logs
           (timestamp, invoice_number, agent, status, decision, flags, total, vendor)
           VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_number, agent, status, decision, flags, total, vendor),
    )
    conn.commit()
    conn.close()


def write_audit_event(
    invoice_number: str,
    stage: str,
    status: str,
    decision: str = "",
    reason: str = "",
    flags: str = "",
    actor: str = "",
    metadata: Optional[str] = None,
    file_name: str = "",
    total: float = 0.0,
    vendor: str = "",
) -> None:
    """Write a structured audit event into `audit_events` and update summary."""
    init_logs_db()
    conn = get_logs_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO audit_events
           (timestamp, invoice_number, file_name, stage, status, decision, reason, flags, actor, metadata, total, vendor)
           VALUES (datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_number, file_name, stage, status, decision, reason, flags, actor, metadata or "", total, vendor),
    )

    # Upsert invoice summary
    cursor.execute(
        """
        INSERT INTO invoice_summaries (invoice_number, first_seen, last_seen, status, total, vendor, events_count)
        VALUES (?, datetime('now'), datetime('now'), ?, ?, ?, 1)
        ON CONFLICT(invoice_number) DO UPDATE SET
          last_seen = datetime('now'),
          status = excluded.status,
          total = COALESCE(excluded.total, invoice_summaries.total),
          vendor = COALESCE(excluded.vendor, invoice_summaries.vendor),
          events_count = invoice_summaries.events_count + 1
        """,
        (invoice_number, status, total, vendor),
    )

    conn.commit()
    conn.close()


def get_audit_events(invoice_number: str) -> list[dict]:
    """Return all audit events for an invoice as a list of dicts."""
    conn = get_logs_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM audit_events WHERE invoice_number = ? ORDER BY id",
        (invoice_number,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def export_audit_csv(path: Path, invoice_number: Optional[str] = None) -> None:
    """Export audit events to a CSV file. Filters by invoice_number when provided."""
    import csv

    conn = get_logs_connection()
    cursor = conn.cursor()
    if invoice_number:
        cursor.execute("SELECT * FROM audit_events WHERE invoice_number = ? ORDER BY id", (invoice_number,))
    else:
        cursor.execute("SELECT * FROM audit_events ORDER BY id")
    rows = cursor.fetchall()
    if not rows:
        conn.close()
        return

    fieldnames = rows[0].keys()
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r[k] for k in fieldnames})
    conn.close()


def get_connection() -> sqlite3.Connection:
    """Get a connection to the inventory database."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables and seed with data from the README spec."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            item TEXT PRIMARY KEY,
            stock INTEGER NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            name TEXT PRIMARY KEY,
            is_known INTEGER DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_invoices (
            invoice_number TEXT PRIMARY KEY,
            revision TEXT DEFAULT '',
            processed_at TEXT,
            status TEXT DEFAULT ''
        )
    """)

    # Seed inventory from README spec
    seed_data = [
        ("WidgetA", 15),
        ("WidgetB", 10),
        ("GadgetX", 5),
        ("FakeItem", 0),
    ]

    for item, stock in seed_data:
        cursor.execute(
            "INSERT OR IGNORE INTO inventory (item, stock) VALUES (?, ?)",
            (item, stock),
        )

    # Seed known vendors
    vendors = [
        "Widgets Inc.",
        "Gadgets Co.",
        "Fraudster LLC",
        "Precision Parts Ltd.",
        "Global Supply Chain Partners",
        "Acme Industrial Supplies",
        "MegaWidgets Corp",
        "NoProd Industries",
        "Consolidated Materials Group",
        "Summit Manufacturing Co.",
        "QuickShip Distributers",
        "Atlas Industrial Supply",
        "TechParts International",
        "Reliable Components Inc.",
    ]
    for v in vendors:
        cursor.execute(
            "INSERT OR IGNORE INTO vendors (name, is_known) VALUES (?, 1)",
            (v,),
        )

    conn.commit()
    conn.close()


def query_inventory(item_name: str) -> Optional[dict]:
    """Query stock for a single item. Returns dict or None if unknown."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT item, stock FROM inventory WHERE item = ?", (item_name,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    return {"item": row["item"], "stock": row["stock"]}


def is_known_vendor(vendor_name: str) -> bool:
    """Check if a vendor is in the known vendors table."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM vendors WHERE name = ?", (vendor_name,))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def mark_invoice_processed(invoice_number: str, revision: str = "", status: str = "") -> None:
    """Record that an invoice has been processed (for duplicate detection)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO processed_invoices
           (invoice_number, revision, processed_at, status)
           VALUES (?, ?, datetime('now'), ?)""",
        (invoice_number, revision, status),
    )
    conn.commit()
    conn.close()


def is_invoice_already_processed(invoice_number: str) -> Optional[str]:
    """Check if an invoice number has been processed before.
    Returns the existing status if found, None otherwise."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status FROM processed_invoices WHERE invoice_number = ?",
        (invoice_number,),
    )
    row = cursor.fetchone()
    conn.close()
    return row["status"] if row else None
