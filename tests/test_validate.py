"""Tests for the validation agent."""

import pytest
from agents.validate_agent import validate_node
from src.state import ExtractedInvoice, LineItem


def _make_state(items, total=100.0, vendor="Widgets Inc.", inv_num="INV-TEST"):
    invoice = ExtractedInvoice(invoice_number=inv_num, vendor=vendor, items=items, total=total)
    return {"extracted_invoice": invoice.model_dump()}


class TestInventoryChecks:
    def test_known_item_within_stock_passes(self):
        result = validate_node(_make_state([LineItem(item="WidgetA", qty=5)]))
        assert result["validation_result"]["passed"] is True

    def test_quantity_exceeds_stock_flagged(self):
        result = validate_node(_make_state([LineItem(item="GadgetX", qty=20)]))
        vr = result["validation_result"]
        assert vr["passed"] is False
        assert "stock_mismatch" in [c["status"] for c in vr["inventory_checks"]]

    def test_zero_stock_item_flagged(self):
        result = validate_node(_make_state([LineItem(item="FakeItem", qty=1)]))
        vr = result["validation_result"]
        assert vr["passed"] is False
        assert "out_of_stock" in [c["status"] for c in vr["inventory_checks"]]

    def test_unknown_item_flagged(self):
        result = validate_node(_make_state([LineItem(item="SuperGizmo", qty=1)]))
        vr = result["validation_result"]
        assert vr["passed"] is False
        assert "unknown_item" in [c["status"] for c in vr["inventory_checks"]]

    def test_duplicate_items_aggregated_correctly(self):
        items = [LineItem(item="WidgetA", qty=8), LineItem(item="WidgetA", qty=4)]
        result = validate_node(_make_state(items))
        vr = result["validation_result"]
        assert vr["passed"] is True
        assert vr["inventory_checks"][0]["requested_qty"] == 12
