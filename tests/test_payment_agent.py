"""Unit tests for the payment agent.

Critical invariant: mock_payment() fires ONLY when final_decision == "approved"
as a hard string check — never from LLM free text.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.payment_agent import run_payment, run_rejection_log, mock_payment
from src.ops_db import init_db, reset_approved_quantities


@pytest.fixture(autouse=True)
def fresh_db():
    init_db()
    reset_approved_quantities()
    yield


def _base_state(decision: str, amount: float = 1000.0) -> dict:
    return {
        "final_decision": decision,
        "extracted_data": {
            "invoice_number": "INV-PAY-TEST",
            "vendor": "Test Vendor",
            "amount": amount,
            "items": [{"name": "WidgetA", "qty": 2}],
        },
        "decision_reasoning": "Test reasoning",
        "run_id": "test_run",
    }


# ── Approved: mock_payment fires ──────────────────────────────────────────────


def test_approved_triggers_payment(caplog):
    import logging

    state = _base_state("approved", amount=2500.0)
    with caplog.at_level(logging.INFO, logger="src.agents.payment_agent"):
        result = run_payment(state)
    assert result["payment_result"]["status"] == "success"
    assert any("2500" in r.message or "PAID" in r.message for r in caplog.records)


def test_approved_records_approved_quantities():
    from src.ops_db import get_cumulative_approved_qty

    state = _base_state("approved", amount=500.0)
    run_payment(state)
    approved_qty = get_cumulative_approved_qty("WidgetA")
    assert approved_qty == 2  # two units of WidgetA recorded


# ── Rejected: no payment ──────────────────────────────────────────────────────


def test_rejected_does_not_pay():
    # run_payment never receives "rejected" in normal graph flow (routed to
    # rejection_log instead), but if it ever does it must not pay.
    state = _base_state("rejected", amount=100000.0)
    result = run_payment(state)
    assert result["payment_result"]["status"] == "error"


def test_rejection_log_writes_log_file(tmp_path):
    # run_rejection_log is the node that writes rejection JSON for both
    # duplicate fast-path and post-approval rejections.
    with patch("src.agents.payment_agent.LOG_DIR", str(tmp_path)):
        state = {
            "extracted_data": {
                "invoice_number": "INV-REJ-TEST",
                "vendor": "Test Vendor",
                "amount": 1000.0,
                "items": [],
            },
            "validation_flags": [
                {
                    "issue_type": "duplicate_invoice",
                    "item": "INV-REJ-TEST",
                    "detail": "Already approved.",
                }
            ],
            "decision_reasoning": "Test reasoning",
            "run_id": "test_run",
        }
        run_rejection_log(state)
        log_files = list(tmp_path.glob("*_rejection.json"))
        assert len(log_files) == 1


# ── Needs review ──────────────────────────────────────────────────────────────


def test_needs_review_does_not_pay():
    state = _base_state("needs_review", amount=4125.0)
    result = run_payment(state)
    assert result["payment_result"]["status"] == "needs_review"


# ── Unknown / unexpected final_decision ───────────────────────────────────────


def test_unexpected_decision_does_not_pay():
    """Any value other than 'approved' must NEVER trigger payment."""
    for bad_val in ("yes", "true", "1", "APPROVED", None, ""):
        state = _base_state(bad_val, amount=99999.0)
        result = run_payment(state)
        assert result["payment_result"]["status"] != "success", (
            f"Payment fired for bad final_decision={bad_val!r}"
        )


# ── Rejection fast-path node ──────────────────────────────────────────────────


def test_rejection_log_node_sets_final_decision():
    state = {
        "extracted_data": {
            "invoice_number": "INV-DUP",
            "vendor": "DupVendor",
            "amount": 5000.0,
            "items": [],
        },
        "validation_flags": [
            {
                "issue_type": "duplicate_invoice",
                "item": "INV-DUP",
                "detail": "Already approved.",
            }
        ],
        "run_id": "test_run",
    }
    result = run_rejection_log(state)
    assert result["final_decision"] == "rejected"
    assert result["payment_result"]["status"] == "rejected"


# ── mock_payment API shape ────────────────────────────────────────────────────


def test_mock_payment_return_shape(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="src.agents.payment_agent"):
        result = mock_payment("Test Corp", 1500.0)
    assert result == {"status": "success"}
    assert any("1500" in r.message and "Test Corp" in r.message for r in caplog.records)
