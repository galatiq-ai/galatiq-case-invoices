import os
from pathlib import Path

from src.database import LOGS_DB_PATH, init_logs_db, get_logs_connection
from src.graph import run_pipeline


def _reset_logs_db():
    try:
        if LOGS_DB_PATH.exists():
            LOGS_DB_PATH.unlink()
    except Exception:
        pass
    init_logs_db()


def test_pipeline_writes_audit_rows():
    _reset_logs_db()

    # Run pipeline on a sample invoice file included in repo
    res = run_pipeline(str(Path("data/invoices/invoice_1001.txt")))
    inv = (res.get("extracted_invoice") or {}).get("invoice_number") or Path("invoice_1001").stem

    conn = get_logs_connection()
    cur = conn.cursor()
    cur.execute("SELECT stage FROM audit_events WHERE invoice_number = ?", (inv,))
    rows = cur.fetchall()
    conn.close()

    stages = {r[0] for r in rows}
    # Expect at least these stages to be present
    assert {"ingest", "validate", "approve", "pipeline_summary"}.issubset(stages)


def test_failure_path_writes_error_audit():
    _reset_logs_db()

    # Run pipeline on a non-existent file to trigger error path
    res = run_pipeline(str(Path("data/invoices/this_file_does_not_exist.txt")))

    # There should be at least one audit row recorded (pipeline_summary or error)
    conn = get_logs_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM audit_events")
    count = cur.fetchone()[0]
    conn.close()

    assert count >= 1
