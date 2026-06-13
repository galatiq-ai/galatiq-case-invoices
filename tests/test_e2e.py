"""End-to-end pipeline tests covering the 5 core README scenarios."""

import pytest
from pathlib import Path
from src.graph import run_pipeline

INVOICES = Path(__file__).resolve().parent.parent / "data" / "invoices"


class TestE2EScenarios:
    def test_inv_1001_normal_passes(self):
        result = run_pipeline(str(INVOICES / "invoice_1001.txt"))
        assert result.get("error") is None
        assert result["approval_decision"]["decision"] in ("approved", "hold")

    def test_inv_1002_stock_mismatch_flagged(self):
        result = run_pipeline(str(INVOICES / "invoice_1002.txt"))
        statuses = [c["status"] for c in result["validation_result"]["inventory_checks"]]
        assert "stock_mismatch" in statuses

    def test_inv_1003_fraudulent_hard_rejected(self):
        result = run_pipeline(str(INVOICES / "invoice_1003.txt"))
        assert result["approval_decision"]["decision"] == "rejected"

    def test_inv_1008_unknown_items_flagged(self):
        result = run_pipeline(str(INVOICES / "invoice_1008.txt"))
        statuses = [c["status"] for c in result["validation_result"]["inventory_checks"]]
        assert "unknown_item" in statuses

    def test_inv_1009_negative_quantity_hard_rejected(self):
        result = run_pipeline(str(INVOICES / "invoice_1009.json"))
        assert result["approval_decision"]["decision"] == "rejected"
        assert "HARD REJECTION" in result["approval_decision"]["reason"]

    def test_nonexistent_file_sets_error_no_crash(self):
        result = run_pipeline("data/invoices/does_not_exist.txt")
        assert result.get("error") is not None
        assert result.get("payment_result") is None
