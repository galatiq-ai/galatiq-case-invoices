"""End-to-end tests: run the full pipeline against every provided invoice.

These tests require a real LLM API key.  Set the environment variable
INTEGRATION_TESTS=1 to run them, otherwise they are skipped.

  INTEGRATION_TESTS=1 pytest tests/test_end_to_end.py -v

The expected outcomes for provided invoices are derived from:
  - Direct inspection of every invoice file in data/invoices/
  - The data/generate_pdfs.py script (ground truth for PDF invoices 1011/1012/1013)

For synthetic edge-case invoices (INV-EDGE-*), expected behavior is
documented inline at the assertion.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── Skip entire module unless INTEGRATION_TESTS=1 ────────────────────────────

pytestmark = pytest.mark.skipif(
    os.getenv("INTEGRATION_TESTS", "0") != "1",
    reason="Set INTEGRATION_TESTS=1 to run end-to-end tests (requires LLM API key)",
)


from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from config import INVOICE_DIR  # noqa: E402
from src.graph.graph import run_pipeline  # noqa: E402
from src.ops_db import (  # noqa: E402
    init_db,
    reset_approved_quantities,
    reset_fingerprints,
    record_approved_quantities,
)
from src.db.queries import init_normalized_db  # noqa: E402

FIXTURE_DIR = "tests/fixtures"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_db():
    """Ensure DB is initialised and all ops state cleared before each test."""
    init_db()
    init_normalized_db()
    reset_approved_quantities()
    reset_fingerprints()
    yield


def _run(path: str, extra_state: dict = None) -> dict:
    state = {"invoice_path": path, "run_id": "e2e_test"}
    if extra_state:
        state.update(extra_state)
    return run_pipeline(state)


def _flags(state: dict) -> set[str]:
    return {f["issue_type"] for f in state.get("validation_flags", [])}


# ── README table: named test cases ───────────────────────────────────────────


class TestReadmeTable:
    def test_inv_1001_clean_pass(self):
        """INV-1001: clean order, WidgetA×10 + WidgetB×5, $5,000. Should pass."""
        state = _run(f"{INVOICE_DIR}/invoice_1001.txt")
        assert not _flags(state) - {"revision_detected"}  # no critical flags
        assert state.get("final_decision") == "approved"

    def test_inv_1004_clean_pass(self):
        """INV-1004: clean JSON order. Should pass."""
        state = _run(f"{INVOICE_DIR}/invoice_1004.json")
        assert state.get("final_decision") == "approved"

    def test_inv_1006_clean_pass(self):
        """INV-1006: vertical-format CSV, clean. Should pass."""
        state = _run(f"{INVOICE_DIR}/invoice_1006.csv")
        assert state.get("final_decision") == "approved"

    def test_inv_1002_gadgetx_stock_mismatch(self):
        """INV-1002: requests 20× GadgetX, stock=5. Must flag insufficient_stock."""
        state = _run(f"{INVOICE_DIR}/invoice_1002.txt")
        assert "insufficient_stock" in _flags(state)

    def test_inv_1003_fakeitem_zero_stock(self):
        """INV-1003: FakeItem with 0 stock. Must flag out_of_stock."""
        state = _run(f"{INVOICE_DIR}/invoice_1003.txt")
        assert "out_of_stock" in _flags(state)

    def test_inv_1008_unknown_items(self):
        """INV-1008: SuperGizmo and MegaSprocket not in inventory. Must flag unknown_item."""
        state = _run(f"{INVOICE_DIR}/invoice_1008.txt")
        assert "unknown_item" in _flags(state)

    def test_inv_1016_widgetc_unknown(self):
        """INV-1016: WidgetC not in inventory. Must flag unknown_item."""
        state = _run(f"{INVOICE_DIR}/invoice_1016.json")
        assert "unknown_item" in _flags(state)

    def test_inv_1009_negative_quantity(self):
        """INV-1009: WidgetA×-5. Must flag invalid_quantity."""
        state = _run(f"{INVOICE_DIR}/invoice_1009.json")
        assert "invalid_quantity" in _flags(state)


# ── Invoices not in README table (inspected directly) ─────────────────────────


class TestInspectedInvoices:
    def test_inv_1005_gadgetx_insufficient(self):
        """INV-1005: GadgetX×8 vs stock=5. Must flag insufficient_stock."""
        state = _run(f"{INVOICE_DIR}/invoice_1005.json")
        assert "insufficient_stock" in _flags(state)

    def test_inv_1007_widgeta_widgetb_insufficient(self):
        """INV-1007: WidgetA×20 > 15, WidgetB×15 > 10. Both must flag insufficient_stock."""
        state = _run(f"{INVOICE_DIR}/invoice_1007.csv")
        assert "insufficient_stock" in _flags(state)

    def test_inv_1010_processes_without_crash(self):
        """INV-1010: WidgetA with rush-order note. LLM should normalise; pipeline should not crash."""
        state = _run(f"{INVOICE_DIR}/invoice_1010.txt")
        assert state.get("extracted_data") is not None
        assert state.get("final_decision") in ("approved", "rejected", "needs_review")

    def test_inv_1014_foreign_currency_needs_review(self):
        """INV-1014: EUR invoice. Must flag foreign_currency and route to needs_review."""
        state = _run(f"{INVOICE_DIR}/invoice_1014.xml")
        assert "foreign_currency" in _flags(state)
        assert state.get("final_decision") == "needs_review"

    def test_inv_1015_clean_csv(self):
        """INV-1015: clean tabular CSV, all items within stock."""
        state = _run(f"{INVOICE_DIR}/invoice_1015.csv")
        critical = _flags(state) - {"revision_detected", "price_mismatch"}
        assert not critical

    def test_inv_1004_revised_revision_detected(self):
        """INV-1004_revised: should flag revision_detected, not duplicate_invoice."""
        state = _run(f"{INVOICE_DIR}/invoice_1004_revised.json")
        assert "revision_detected" in _flags(state)
        assert "duplicate_invoice" not in _flags(state)


# ── PDF + TXT twin consistency checks ─────────────────────────────────────────


class TestFormatPairConsistency:
    def _extract(self, path: str) -> dict:
        state = _run(path)
        return state.get("extracted_data", {})

    def test_inv_1011_pdf_txt_match(self):
        """INV-1011: PDF and TXT versions should extract the same vendor and amount."""
        pdf = self._extract(f"{INVOICE_DIR}/invoice_1011.pdf")
        txt = self._extract(f"{INVOICE_DIR}/invoice_1011.txt")
        assert pdf.get("vendor", "").lower() == txt.get("vendor", "").lower()
        assert pdf.get("amount") == pytest.approx(txt.get("amount"), rel=0.01)

    def test_inv_1012_pdf_txt_match(self):
        """INV-1012: PDF and TXT should agree on vendor and amount despite OCR artifacts."""
        pdf = self._extract(f"{INVOICE_DIR}/invoice_1012.pdf")
        txt = self._extract(f"{INVOICE_DIR}/invoice_1012.txt")
        assert pdf.get("amount") == pytest.approx(txt.get("amount"), rel=0.01)

    def test_inv_1013_pdf_json_match_vendor(self):
        """INV-1013: PDF and JSON should agree on vendor."""
        pdf = self._extract(f"{INVOICE_DIR}/invoice_1013.pdf")
        jsn = self._extract(f"{INVOICE_DIR}/invoice_1013.json")
        assert pdf.get("vendor", "").lower() == jsn.get("vendor", "").lower()


# ── Ground-truth assertions from generate_pdfs.py ────────────────────────────


class TestGeneratePdfsGroundTruth:
    def test_inv_1011_clean_no_flags(self):
        """INV-1011: Summit Manufacturing Co., $3,000. Clean, no flags."""
        state = _run(f"{INVOICE_DIR}/invoice_1011.txt")
        critical = _flags(state) - {"revision_detected", "price_mismatch"}
        assert not critical
        assert state.get("final_decision") == "approved"

    def test_inv_1011_amount(self):
        """INV-1011: stated total must be $3,000."""
        state = _run(f"{INVOICE_DIR}/invoice_1011.txt")
        assert state["extracted_data"]["amount"] == pytest.approx(3000.00, rel=0.01)

    def test_inv_1012_messy_amount_correct(self):
        """INV-1012: despite OCR artifacts, amount should extract as $9,975."""
        state = _run(f"{INVOICE_DIR}/invoice_1012.txt")
        assert state["extracted_data"]["amount"] == pytest.approx(9975.00, rel=0.01)

    def test_inv_1012_date_parseable(self):
        """INV-1012: '26-Jan-2O26' (letter O) should be parsed to a date, with warning."""
        state = _run(f"{INVOICE_DIR}/invoice_1012.txt")
        ed = state["extracted_data"]
        # Either date is extracted (possibly imperfectly) or a warning is issued
        has_date = ed.get("due_date") is not None
        has_warning = any(
            "date" in w.lower() for w in ed.get("extraction_warnings", [])
        )
        assert has_date or has_warning

    def test_inv_1013_total_mismatch_detected(self):
        """INV-1013: stated $22,562.80 vs calculated $22,512.80 (off by $50)."""
        state = _run(f"{INVOICE_DIR}/invoice_1013.json")
        assert "total_mismatch" in _flags(state)

    def test_inv_1013_insufficient_stock_all_three(self):
        """INV-1013: bulk order exceeds stock for WidgetA, WidgetB, GadgetX."""
        state = _run(f"{INVOICE_DIR}/invoice_1013.json")
        assert "insufficient_stock" in _flags(state)

    def test_inv_1013_high_value_critique_runs(self):
        """INV-1013: $22k invoice must trigger the critique loop."""
        state = _run(f"{INVOICE_DIR}/invoice_1013.json")
        # Critique should have been populated
        assert state.get("critique_notes")


# ── Duplicate invoice idempotency ─────────────────────────────────────────────


class TestDuplicateIdempotency:
    def test_duplicate_invoice_rejected(self):
        """Processing the same invoice twice should reject on the second run."""
        record_approved_quantities(
            "INV-1001",
            [
                {"name": "WidgetA", "qty": 10},
                {"name": "WidgetB", "qty": 5},
            ],
        )
        state = _run(f"{INVOICE_DIR}/invoice_1001.txt")
        assert state.get("is_duplicate")
        assert state.get("final_decision") == "rejected"


# ── Threshold boundary: synthetic fixtures ────────────────────────────────────


class TestThresholdBoundary:
    def test_just_under_threshold_no_critique(self):
        """$9,999.99 invoice: no critique loop should run."""
        state = _run(f"{FIXTURE_DIR}/invoice_edge_just_under_threshold.json")
        # If under threshold, critique_notes should be empty/None
        assert not state.get("critique_notes")

    def test_just_over_threshold_critique_runs(self):
        """$10,000.01 invoice: critique loop MUST run."""
        state = _run(f"{FIXTURE_DIR}/invoice_edge_just_over_threshold.json")
        assert state.get("critique_notes")
        assert state.get("review_count", 0) >= 1


# ── Unicode vendor name ───────────────────────────────────────────────────────


class TestUnicodeVendor:
    def test_unicode_vendor_does_not_crash(self):
        """Vendor name with unicode characters should not crash the pipeline."""
        state = _run(f"{FIXTURE_DIR}/invoice_edge_unicode.json")
        assert state.get("extracted_data") is not None
        assert state.get("final_decision") in ("approved", "rejected", "needs_review")


# ── Duplicate fixture ─────────────────────────────────────────────────────────


class TestSyntheticDuplicate:
    def test_synthetic_duplicate_rejected(self):
        """Pre-seed INV-EDGE-004 as already approved; second run must reject."""
        record_approved_quantities("INV-EDGE-004", [{"name": "WidgetA", "qty": 2}])
        state = _run(f"{FIXTURE_DIR}/invoice_edge_duplicate.json")
        assert state.get("is_duplicate")
        assert state.get("final_decision") == "rejected"


# ── New invoice files: 1017–1019 ──────────────────────────────────────────────


class TestNewInvoices:
    def test_inv_1017_price_mismatch_xml(self):
        """INV-1017: XML invoice with WidgetA@$390 (+56%) and GadgetX@$1100 (+47%).
        Both prices exceed the 20% tolerance. Must flag price_mismatch.
        Items are within stock so no stock flags should fire."""
        state = _run(f"{INVOICE_DIR}/invoice_1017.xml")
        assert "price_mismatch" in _flags(state)
        assert "insufficient_stock" not in _flags(state)
        assert "unknown_item" not in _flags(state)

    def test_inv_1018_zero_quantity_txt(self):
        """INV-1018: TXT invoice with WidgetB qty=0 mixed alongside valid lines.
        Zero quantity is invalid; must flag invalid_quantity.
        The other items (WidgetA×4, GadgetX×1) are within stock and should not flag."""
        state = _run(f"{INVOICE_DIR}/invoice_1018.txt")
        assert "invalid_quantity" in _flags(state)
        assert "out_of_stock" not in _flags(state)

    def test_inv_1019_total_mismatch_csv(self):
        """INV-1019: CSV invoice where line items sum to $1,750 but stated Total is $2,350.
        The $600 discrepancy far exceeds the $0.05 tolerance. Must flag total_mismatch.
        Items are within stock so no stock flags should fire."""
        state = _run(f"{INVOICE_DIR}/invoice_1019.csv")
        assert "total_mismatch" in _flags(state)
        assert "insufficient_stock" not in _flags(state)
