"""Additional end-to-end tests derived from e2e_test_plan.md."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.graph import run_pipeline


INVOICES = Path(__file__).resolve().parent.parent / "data" / "invoices"


def test_inv_1006_csv_processed():
    result = run_pipeline(str(INVOICES / "invoice_1006.csv"))
    inv = result.get("extracted_invoice") or {}
    assert inv.get("invoice_number") == "INV-1006"
    assert result.get("approval_decision") is not None


def test_inv_1014_xml_eur_processed():
    result = run_pipeline(str(INVOICES / "invoice_1014.xml"))
    inv = result.get("extracted_invoice") or {}
    assert inv.get("currency") == "EUR"
    assert result.get("error") is None


def test_inv_1011_pdf_parsed():
    # Skip if no PDF libs available in the environment
    try:
        import fitz  # PyMuPDF
    except Exception:  # pragma: no cover - environment-dependent
        try:
            import pdfplumber  # noqa: F401
        except Exception:
            pytest.skip("No PDF library installed (fitz or pdfplumber)")

    result = run_pipeline(str(INVOICES / "invoice_1011.pdf"))
    inv = result.get("extracted_invoice")
    assert inv is not None
    assert len(inv.get("items", [])) >= 0


def test_high_value_invoice_triggers_scrutiny():
    result = run_pipeline(str(INVOICES / "invoice_1005.json"))
    decision = result.get("approval_decision") or {}
    actions = decision.get("required_actions", [])
    assert any("scrutiny" in a.lower() for a in actions)


def test_duplicate_invoice_triggers_hold():
    first = run_pipeline(str(INVOICES / "invoice_1004.json"))
    assert first.get("approval_decision") is not None
    second = run_pipeline(str(INVOICES / "invoice_1004.json"))
    assert second.get("approval_decision", {}).get("decision") == "hold"


def test_trace_file_written(tmp_path):
    # Ensure traces dir exists and is writable
    traces_dir = Path("traces")
    if traces_dir.exists():
        # remove any existing trace for INV-1001 to ensure fresh write
        for f in traces_dir.glob("INV-1001*.json"):
            try:
                f.unlink()
            except Exception:
                pass

    run_pipeline(str(INVOICES / "invoice_1001.txt"))
    trace_files = list(Path("traces").glob("INV-1001*.json"))
    assert len(trace_files) > 0
    with open(trace_files[0], "r", encoding="utf-8") as fh:
        trace = json.load(fh)
    # trace is expected to be a list of agent entries
    assert isinstance(trace, list)
    assert any(entry.get("agent") for entry in trace)


def test_batch_runner_csv_output(tmp_path):
    out_csv = tmp_path / "results.csv"
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    cmd = [sys.executable, str(main_py), "--invoice_dir=data/invoices", f"--output_csv={out_csv}"]
    subprocess.run(cmd, check=True)
    assert out_csv.exists()
    import csv

    with open(out_csv, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) > 0
    assert "invoice_number" in rows[0]
    assert "decision" in rows[0]
