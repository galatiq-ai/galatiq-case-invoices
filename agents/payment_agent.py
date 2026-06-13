"""Payment Agent — executes mock payment and records transaction.

Only runs if the invoice is approved. Logs rejection otherwise.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.database import mark_invoice_processed, log_agent_event
from src.state import (
    ApprovalDecision,
    ExtractedInvoice,
    PaymentResult,
)
from src.tools import mock_payment, write_trace, get_error_context
import src.config as config
from src.audit import record_stage_event


def payment_node(state: dict) -> dict:
    """LangGraph node: execute payment if approved, log if rejected."""
    extracted_data = state.get("extracted_invoice")
    approval_data = state.get("approval_decision")

    if not extracted_data or not approval_data:
        state["error"] = "Missing invoice or approval data for payment stage"
        return state

    invoice = ExtractedInvoice(**extracted_data)
    decision = ApprovalDecision(**approval_data)

    if decision.decision == "approved":
        # Execute payment
        try:
            result = mock_payment(invoice.vendor, invoice.total)
            payment = PaymentResult(
                status="success",
                tx_id=result.get("tx_id"),
                vendor=invoice.vendor,
                amount=invoice.total,
            )
            state["payment_result"] = payment.model_dump()
            mark_invoice_processed(
                invoice.invoice_number,
                status="paid",
            )
            print(f"  [PAYMENT] [SUCCESS] | {invoice.vendor} | "
                  f"${invoice.total:,.2f} | TX {result.get('tx_id', 'N/A')}")
        except Exception as e:
            payment = PaymentResult(
                status="failed",
                vendor=invoice.vendor,
                amount=invoice.total,
                error=get_error_context(e),
            )
            state["payment_result"] = payment.model_dump()
            state["error"] = f"Payment failed: {get_error_context(e)}"
            print(f"  [PAYMENT] [FAILED] | {get_error_context(e)}")

    elif decision.decision == "rejected":
        payment = PaymentResult(
            status="skipped",
            vendor=invoice.vendor,
            amount=invoice.total,
            error=f"Invoice rejected: {decision.reason[:100]}",
        )
        state["payment_result"] = payment.model_dump()
        mark_invoice_processed(
            invoice.invoice_number,
            status="rejected",
        )
        print(f"  [PAYMENT] [SKIPPED] | Rejected: {decision.reason[:80]}")

    elif decision.decision == "hold":
        payment = PaymentResult(
            status="skipped",
            vendor=invoice.vendor,
            amount=invoice.total,
            error=f"Invoice on hold: {decision.reason[:100]}",
        )
        state["payment_result"] = payment.model_dump()
        mark_invoice_processed(
            invoice.invoice_number,
            status="hold",
        )
        print(f"  [PAYMENT] [HOLD] | {decision.reason[:80]}")

    write_trace(state, "payment_agent")
    if config.VERBOSE:
        pr = state.get("payment_result", {}) or {}
        print(f"    [PAYMENT DETAIL] Result: {pr}")
    try:
        pr = state.get("payment_result", {}) or {}
        # Record audit event (best-effort)
        try:
            file_path = state.get("file_path", "")
            record_stage_event(
                invoice_number=invoice.invoice_number,
                file_name=(file_path and Path(file_path).name) or "",
                stage="payment",
                status=pr.get("status", "unknown"),
                decision=decision.decision,
                reason=pr.get("error", "") or "",
                actor="payment_agent",
                metadata={"tx_id": pr.get("tx_id")},
                total=invoice.total,
                vendor=invoice.vendor,
            )
        except Exception:
            pass

        log_agent_event(
            invoice_number=invoice.invoice_number,
            agent="payment_agent",
            status=pr.get("status", "unknown"),
            decision=decision.decision,
            flags="",
            total=invoice.total,
            vendor=invoice.vendor,
        )
    except Exception:
        pass
    return state
