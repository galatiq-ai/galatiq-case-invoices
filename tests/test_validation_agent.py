"""Unit tests for the validation agent.

All validation logic is deterministic Python — no LLM mocking needed.
Tests initialise a fresh in-memory-like DB state for each test.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ops_db import init_db, reset_approved_quantities
from src.agents.validation_agent import run_validation


@pytest.fixture(autouse=True)
def fresh_db():
    """Initialise DB and clear approved quantities before each test."""
    init_db()
    reset_approved_quantities()
    yield


def _state(
    items: list[dict],
    amount: float = None,
    tax_amount: float = None,
    invoice_number: str = "INV-TEST",
    currency: str = "USD",
    invoice_path: str = "data/invoices/invoice_test.json",
) -> dict:
    return {
        "invoice_path": invoice_path,
        "extracted_data": {
            "invoice_number": invoice_number,
            "vendor": "Test Vendor",
            "amount": amount,
            "currency": currency,
            "items": items,
            "tax_amount": tax_amount,
            "extraction_warnings": [],
        },
    }


def _flag_types(result: dict) -> list[str]:
    return [f["issue_type"] for f in result.get("validation_flags", [])]


# ── Clean invoice ─────────────────────────────────────────────────────────────


def test_clean_invoice_no_flags():
    s = _state([{"name": "WidgetA", "qty": 5, "unit_price": 250.00}], amount=1250.00)
    result = run_validation(s)
    assert result["validation_flags"] == []
    assert not result["is_duplicate"]


# ── Unknown item ──────────────────────────────────────────────────────────────


def test_unknown_item_flagged():
    s = _state(
        [
            {"name": "SuperGizmo", "qty": 12, "unit_price": 400.00},
            {"name": "MegaSprocket", "qty": 6, "unit_price": 850.00},
        ],
        amount=9900.00,
    )
    result = run_validation(s)
    types = _flag_types(result)
    assert types.count("unknown_item") == 2


# ── Out of stock ──────────────────────────────────────────────────────────────


def test_out_of_stock_flagged():
    # FakeItem has stock=0
    s = _state(
        [{"name": "FakeItem", "qty": 100, "unit_price": 1000.00}], amount=100000.00
    )
    result = run_validation(s)
    assert "out_of_stock" in _flag_types(result)


# ── Insufficient stock ────────────────────────────────────────────────────────


def test_insufficient_stock_gadgetx():
    # GadgetX has stock=5, requesting 20
    s = _state([{"name": "GadgetX", "qty": 20, "unit_price": 750.00}], amount=15000.00)
    result = run_validation(s)
    assert "insufficient_stock" in _flag_types(result)


def test_within_stock_no_flag():
    # WidgetA stock=15, requesting 10 — should pass
    s = _state([{"name": "WidgetA", "qty": 10, "unit_price": 250.00}], amount=2500.00)
    result = run_validation(s)
    assert "insufficient_stock" not in _flag_types(result)


# ── Invalid quantity ──────────────────────────────────────────────────────────


def test_negative_quantity_flagged():
    s = _state([{"name": "WidgetA", "qty": -5, "unit_price": 250.00}])
    result = run_validation(s)
    assert "invalid_quantity" in _flag_types(result)


def test_zero_quantity_flagged():
    s = _state([{"name": "WidgetA", "qty": 0, "unit_price": 250.00}])
    result = run_validation(s)
    assert "invalid_quantity" in _flag_types(result)


# ── Total mismatch ────────────────────────────────────────────────────────────


def test_total_mismatch_inv1013():
    """Mirrors INV-1013: stated total $22,562.80, computed $22,512.80 — $50 off."""
    items = [
        {"name": "WidgetA", "qty": 15, "unit_price": 250.00},
        {"name": "WidgetB", "qty": 10, "unit_price": 500.00},
        {"name": "GadgetX", "qty": 5, "unit_price": 750.00},
        {"name": "WidgetA", "qty": 5, "unit_price": 240.00},
        {"name": "WidgetB", "qty": 8, "unit_price": 480.00},
        {"name": "GadgetX", "qty": 3, "unit_price": 750.00},
        {"name": "WidgetA", "qty": 2, "unit_price": 250.00},
        {"name": "GadgetX", "qty": 1, "unit_price": 750.00},
    ]
    # Correct total: 21040 subtotal + 1472.80 (7% tax) = 22512.80
    # Inflated total (as in generate_pdfs.py): 22562.80
    s = _state(items, amount=22562.80, tax_amount=1472.80)
    result = run_validation(s)
    assert "total_mismatch" in _flag_types(result)


def test_total_correct_no_mismatch():
    # WidgetA×10 @ $250 = $2,500, no tax -> total $2,500
    s = _state(
        [{"name": "WidgetA", "qty": 10, "unit_price": 250.00}],
        amount=2500.00,
        tax_amount=0.0,
    )
    result = run_validation(s)
    assert "total_mismatch" not in _flag_types(result)


# ── Duplicate invoice ─────────────────────────────────────────────────────────


def test_duplicate_invoice_rejected():
    """An invoice number already in approved_quantities should flag as duplicate."""
    from src.ops_db import record_approved_quantities

    record_approved_quantities("INV-DUP", [{"name": "WidgetA", "qty": 2}])

    s = _state(
        [{"name": "WidgetA", "qty": 2, "unit_price": 250.00}],
        invoice_number="INV-DUP",
        amount=500.00,
    )
    result = run_validation(s)
    assert result["is_duplicate"]
    assert "duplicate_invoice" in _flag_types(result)


def test_revised_invoice_not_flagged_as_duplicate():
    """A _revised invoice should get revision_detected, not duplicate_invoice."""
    from src.ops_db import record_approved_quantities

    record_approved_quantities("INV-1004", [{"name": "WidgetA", "qty": 3}])

    s = _state(
        [{"name": "WidgetA", "qty": 3, "unit_price": 250.00}],
        invoice_number="INV-1004",
        amount=750.00,
        invoice_path="data/invoices/invoice_1004_revised.json",
    )
    result = run_validation(s)
    assert not result["is_duplicate"]
    assert "revision_detected" in _flag_types(result)
    assert "duplicate_invoice" not in _flag_types(result)


# ── Foreign currency ──────────────────────────────────────────────────────────


def test_foreign_currency_flagged():
    s = _state(
        [{"name": "WidgetA", "qty": 4, "unit_price": 225.00}],
        amount=4125.00,
        currency="EUR",
    )
    result = run_validation(s)
    assert "foreign_currency" in _flag_types(result)


# ── Cumulative stock ──────────────────────────────────────────────────────────


def test_cumulative_stock_check():
    """WidgetA has 15 in stock; after approving 10, a second order for 6 should fail."""
    from src.ops_db import record_approved_quantities

    record_approved_quantities("INV-PREV", [{"name": "WidgetA", "qty": 10}])

    s = _state(
        [{"name": "WidgetA", "qty": 6, "unit_price": 250.00}],
        invoice_number="INV-NEW",
        amount=1500.00,
    )
    result = run_validation(s)
    assert "insufficient_stock" in _flag_types(result)


def test_cumulative_stock_within_limit():
    """After approving 5, an order for 10 more on WidgetA (stock=15) should pass."""
    from src.ops_db import record_approved_quantities

    record_approved_quantities("INV-PREV", [{"name": "WidgetA", "qty": 5}])

    s = _state(
        [{"name": "WidgetA", "qty": 10, "unit_price": 250.00}],
        invoice_number="INV-NEW",
        amount=2500.00,
    )
    result = run_validation(s)
    assert "insufficient_stock" not in _flag_types(result)


# ── Grouped items within same invoice ─────────────────────────────────────────


def test_grouped_items_cumulative_quantity():
    """Multiple line items for the same item in one invoice are summed before checking."""
    # WidgetA stock=15; two lines totalling 16 should fail
    s = _state(
        [
            {"name": "WidgetA", "qty": 10, "unit_price": 250.00},
            {"name": "WidgetA", "qty": 6, "unit_price": 240.00},
        ],
        amount=4000.00,
    )
    result = run_validation(s)
    assert "insufficient_stock" in _flag_types(result)
