"""Tests for the payment agent."""

import pytest
from agents.payment_agent import payment_node
from src.state import ExtractedInvoice, LineItem, ApprovalDecision


def _make_state(decision_str):
    invoice = ExtractedInvoice(
        invoice_number="INV-PAY-TEST", vendor="Widgets Inc.",
        items=[LineItem(item="WidgetA", qty=5)], total=5000.0,
    )
    decision = ApprovalDecision(decision=decision_str, reason="test")
    return {"extracted_invoice": invoice.model_dump(), "approval_decision": decision.model_dump()}


class TestPaymentNode:
    def test_approved_succeeds(self):
        result = payment_node(_make_state("approved"))
        pr = result["payment_result"]
        assert pr["status"] == "success"
        assert pr["tx_id"] is not None

    def test_rejected_skipped(self):
        assert payment_node(_make_state("rejected"))["payment_result"]["status"] == "skipped"

    def test_hold_skipped(self):
        assert payment_node(_make_state("hold"))["payment_result"]["status"] == "skipped"

    def test_approved_marks_paid_in_db(self):
        from src.database import is_invoice_already_processed
        payment_node(_make_state("approved"))
        assert is_invoice_already_processed("INV-PAY-TEST") == "paid"
