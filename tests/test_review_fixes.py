"""Regression tests for the PR #4 review findings."""

import dataclasses
import io
import logging
import shutil
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from invoice_pipeline import cli, ledger, service

CORPUS = Path(__file__).resolve().parent.parent / "data" / "invoices"


def _list_queue(tmp_path, ledger_path):
    out = io.StringIO()
    argv = ["review", "--list", "--ledger", str(ledger_path)]
    code = cli.main([*argv, "--inventory", str(tmp_path / "inventory.db")], out=out)
    return code, out.getvalue()


def test_review_list_on_a_missing_ledger_fails_and_creates_nothing(tmp_path):
    missing = tmp_path / "typo.db"

    code, _ = _list_queue(tmp_path, missing)

    assert code == cli.EXIT_FAILED
    assert not missing.exists()
    assert not (tmp_path / "inventory.db").exists()


def test_review_list_reads_an_existing_ledger_without_seeding_inventory(tmp_path):
    path = tmp_path / "ledger.db"
    ledger.connect(path).close()

    code, output = _list_queue(tmp_path, path)

    assert code == cli.EXIT_OK
    assert output == ""
    assert not (tmp_path / "inventory.db").exists()


def _runtime(tmp_path, **changes) -> service.Runtime:
    args = SimpleNamespace(llm=None, ledger=tmp_path / "l.db", inventory=tmp_path / "i.db")
    return dataclasses.replace(service.bootstrap(args), **changes)


def test_an_observer_error_never_turns_a_committed_payment_into_a_failure(tmp_path, caplog):
    def broken_observer(event):
        raise RuntimeError(f"display broke on {event.name}")

    rt = _runtime(tmp_path, on_event=broken_observer)
    paths = [shutil.copy(CORPUS / "invoice_1015.csv", tmp_path)]

    with caplog.at_level(logging.WARNING):
        batch = service.run_batch(paths, rt)

    assert batch.failed == []
    assert [(r.invoice_number, r.state) for r in batch.results] == [("INV-1015", "paid")]
    assert "payment_sent" in caplog.text


def test_a_failing_invoice_keeps_the_other_invoices_of_its_file(tmp_path, monkeypatch):
    real_validate = service.validate

    def validate(invoice, catalog):
        if invoice.invoice_number == "INV-9001":
            raise ValueError("boom")
        return real_validate(invoice, catalog)

    monkeypatch.setattr(service, "validate", validate)
    text = (CORPUS / "invoice_1015.csv").read_text()
    rows = [line for line in text.splitlines()[1:] if line.startswith("INV-1015")][:1]
    for number in ("INV-9001", "INV-9002"):
        text += "".join(f"{row.replace('INV-1015', number)}\n" for row in rows)
    path = tmp_path / "three.csv"
    path.write_text(text)

    batch = service.run_batch([path], _runtime(tmp_path))

    assert [r.invoice_number for r in batch.results] == ["INV-1015", "INV-9002"]
    assert batch.results[0].state == "paid"
    assert [(f.file, f.stage) for f in batch.failed] == [("three.csv", "validation")]


def test_an_interrupted_ledger_initialisation_leaves_nothing_half_made(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(ledger, "_SCHEMA", ledger._SCHEMA + "; NOT VALID SQL")
    with pytest.raises(sqlite3.Error):
        ledger.connect(path)
    monkeypatch.undo()

    conn = ledger.connect(path)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == ledger.SCHEMA_VERSION
    conn.close()


def test_concurrent_first_connects_all_open_one_initialised_ledger(tmp_path):
    path = tmp_path / "ledger.db"
    start, errors = threading.Barrier(8), []

    def first_connect():
        start.wait()
        try:
            ledger.connect(path).close()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=first_connect) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    conn = ledger.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == ledger.SCHEMA_VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()
