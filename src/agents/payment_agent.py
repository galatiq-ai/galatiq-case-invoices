"""Stage 4: fire payment or log rejection based on final_decision.

Two nodes:
  run_payment       - reached only for approved / needs_review outcomes.
                      mock_payment() fires only on exact string "approved".
  run_rejection_log - reached for duplicates (fast-path) AND post-approval rejections.
                      Bypasses run_payment entirely.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from config import LOG_DIR
from src.db.queries import record_invoice_run
from src.graph.state import InvoiceState
from src.ops_db import (
    compute_fingerprint,
    record_approved_quantities,
    record_fingerprint,
)

logger = logging.getLogger(__name__)


def mock_payment(vendor: str, amount: float) -> dict:
    """Simulates payment execution — logs confirmation and returns status."""
    logger.info("PAID %.2f to %s", amount, vendor)
    return {"status": "success"}


def _append_needs_review_queue(
    invoice_number: str,
    vendor: str,
    amount: float,
    reasoning: str,
    run_id: str,
) -> None:
    """Append this invoice to the needs-review queue (JSON lines file).

    Each line is an independent JSON object so the file is append-safe
    across concurrent runs and readable by any downstream system.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    queue_path = os.path.join(LOG_DIR, "needs_review_queue.jsonl")
    entry = {
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "invoice_number": invoice_number,
        "vendor": vendor,
        "amount": amount,
        "reasoning": reasoning,
        "status": "pending",
    }
    with open(queue_path, "a") as fh:
        fh.write(json.dumps(entry) + "\n")
    logger.info(
        "Needs-review queue entry written to %s (run_id=%s)", queue_path, run_id
    )


def _write_rejection_log(
    invoice_number: str,
    vendor: str,
    amount: float,
    reasoning: str,
    run_id: str,
) -> None:
    """Persist rejection details to a structured log file."""
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"{run_id}_rejection.json")
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "invoice_number": invoice_number,
        "vendor": vendor,
        "amount": amount,
        "reasoning": reasoning,
    }
    with open(log_path, "w") as fh:
        json.dump(payload, fh, indent=2)
    logger.info("Rejection logged to %s", log_path)


def run_payment(state: InvoiceState) -> dict[str, Any]:
    """LangGraph node: execute payment for approved invoices or queue for manual review.

    Reads ``final_decision`` set by the approval agent — never infers it from
    free text. Approved invoices fire mock_payment(), record the fingerprint,
    and persist approved quantities. needs_review invoices are queued without
    payment. Unexpected states are refused and logged as errors.
    """
    final_decision: str | None = state.get("final_decision")
    extracted: dict = state.get("extracted_data", {})
    reasoning: str = state.get("decision_reasoning", "")
    run_id: str = state.get("run_id", "unknown")

    vendor: str = extracted.get("vendor") or "Unknown Vendor"
    amount: float = extracted.get("amount") or 0.0
    invoice_number: str = extracted.get("invoice_number") or ""
    items: list[dict] = extracted.get("items", [])

    # Use delta amount for revisions of already-paid invoices
    delta_amount: float | None = state.get("revision_delta_amount")
    pay_amount = delta_amount if delta_amount is not None else amount

    inv_label = invoice_number or "UNKNOWN"
    logger.info(
        "Payment node: final_decision=%s, vendor=%s, pay_amount=%.2f%s",
        final_decision,
        vendor,
        pay_amount,
        f" (delta of {amount:.2f})" if delta_amount is not None else "",
    )

    if final_decision == "approved":
        result = mock_payment(vendor, pay_amount)
        logger.info("Payment executed: %s -> %s (%.2f)", inv_label, vendor, pay_amount)

        # Record approved quantities for cumulative stock tracking
        valid_items = [
            i for i in items if isinstance(i.get("qty"), int) and i["qty"] > 0
        ]
        if invoice_number and valid_items:
            record_approved_quantities(invoice_number, valid_items)

        # Persist content fingerprint so re-submissions of the same invoice
        # (even with a different invoice number) are caught by validation.
        due_date = extracted.get("due_date") or ""
        fp = compute_fingerprint(vendor, amount, due_date)
        record_fingerprint(fp, invoice_number, vendor)

        # Persist to normalized schema (non-fatal if it fails)
        record_invoice_run(state)

        pay_note = (
            f"delta paid {pay_amount:.2f} to {vendor} (revision of {amount:.2f}; fingerprint recorded)"
            if delta_amount is not None
            else f"paid {pay_amount:.2f} to {vendor} (fingerprint recorded)"
        )
        return {
            "payment_result": {**result, "vendor": vendor, "amount": pay_amount},
            "audit_log": [
                {
                    "stage": "payment",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "ok",
                    "note": pay_note,
                }
            ],
        }

    elif final_decision == "needs_review":
        logger.warning(
            "Invoice %s routed to NEEDS_REVIEW: %s (%.2f)",
            inv_label,
            vendor,
            amount,
        )
        _append_needs_review_queue(inv_label, vendor, amount, reasoning, run_id)
        record_invoice_run(state)
        return {
            "payment_result": {
                "status": "needs_review",
                "vendor": vendor,
                "amount": amount,
                "reasoning": reasoning,
            },
            "audit_log": [
                {
                    "stage": "payment",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "warning",
                    "note": f"routed to manual review: {vendor} {amount:.2f}",
                }
            ],
        }

    else:
        # Unexpected state — fail safe by not paying
        logger.error(
            "Payment node: unexpected final_decision='%s', refusing payment",
            final_decision,
        )
        return {
            "payment_result": {
                "status": "error",
                "detail": f"Unexpected final_decision value: {final_decision}",
            },
            "audit_log": [
                {
                    "stage": "payment",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "note": f"unexpected final_decision: {final_decision}",
                }
            ],
        }


def run_rejection_log(state: InvoiceState) -> dict[str, Any]:
    """LangGraph node: write a rejection log and set payment_result to rejected.

    Reached by two paths: duplicate invoices short-circuit here directly from
    validation; post-approval rejections arrive here after the approval node
    sets final_decision = "rejected".
    """
    extracted: dict = state.get("extracted_data", {})
    flags: list = state.get("validation_flags", [])
    run_id: str = state.get("run_id", "unknown")

    vendor: str = extracted.get("vendor") or "Unknown Vendor"
    amount: float = extracted.get("amount") or 0.0
    invoice_number: str = extracted.get("invoice_number") or ""

    dup_flag = next(
        (f for f in flags if f.get("issue_type") == "duplicate_invoice"), None
    )
    # Duplicate path: use the flag detail.
    # LLM-rejection path: use the VP's actual reasoning already in state —
    # never fall back to a generic string that buries the real decision.
    reasoning = (
        dup_flag["detail"]
        if dup_flag
        else (state.get("decision_reasoning") or "Rejected at approval stage.")
    )

    inv_label = invoice_number or "UNKNOWN"
    _write_rejection_log(inv_label, vendor, amount, reasoning, run_id)
    is_fast_path = bool(dup_flag)
    if is_fast_path:
        logger.info("Rejection (duplicate fast-path): %s", inv_label)
    else:
        logger.info("Rejection (VP decision): %s", inv_label)

    # Persist to normalized schema with the resolved final_decision
    record_invoice_run(
        {**state, "final_decision": "rejected", "decision_reasoning": reasoning}
    )

    return {
        "final_decision": "rejected",
        "decision_reasoning": reasoning,
        "payment_result": {
            "status": "rejected",
            "vendor": vendor,
            "amount": amount,
            "reasoning": reasoning,
        },
        "audit_log": [
            {
                "stage": "payment",
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "warning",
                "note": f"rejected: {inv_label} ({vendor})",
            }
        ],
    }
