from __future__ import annotations

from typing import Any, Dict, List
from pathlib import Path

def _compute_status(result: dict) -> str:
    if result.get("error"):
        return "error"

    approval = result.get("approval_decision") or {}
    decision = approval.get("decision") if isinstance(approval, dict) else None
    payment = result.get("payment_result") or {}

    if decision == "approved":
        if payment and isinstance(payment, dict) and payment.get("status") == "success":
            return "paid"
        return "approved"
    if decision == "hold":
        return "hold"
    if decision == "rejected":
        return "rejected"
    return "unknown"


def summarize_result(result: dict) -> Dict[str, Any]:
    """Convert a pipeline result into a UI-friendly summary structure.

    The returned dict contains:
    - header: invoice_number, vendor, total, currency, status
    - cards: extraction, validation, approval, payment
    - timeline: list of steps with statuses
    - explanation: plain-language strings describing next actions / reasons
    - raw: original sections for collapsible technical view
    """
    inv = result.get("extracted_invoice") or {}

    header = {
        "invoice_number": inv.get("invoice_number") or Path(result.get("file_path", "")).stem,
        "vendor": inv.get("vendor"),
        "total": inv.get("total"),
        "currency": inv.get("currency", "USD"),
        "status": _compute_status(result),
    }

    extraction = {"present": bool(inv), "summary": (inv.get("summary") if isinstance(inv, dict) else None)}
    validation = result.get("validation_result") or {}
    approval = result.get("approval_decision") or {}
    payment = result.get("payment_result") or {}

    cards = {
        "extraction": extraction,
        "validation": {"passed": bool(validation.get("passed")), "summary": validation.get("summary")},
        "approval": {"decision": approval.get("decision"), "reason": approval.get("reason"), "hints": approval.get("correction_hints", [])},
        "payment": {"status": payment.get("status"), "tx_id": payment.get("tx_id"), "error": payment.get("error")},
    }

    # timeline steps
    def step_status(name: str) -> str:
        if name == "ingest":
            return "success" if inv else ("error" if result.get("error") else "pending")
        if name == "validate":
            if not validation:
                return "pending" if inv else "skipped"
            return "success" if validation.get("passed") else "failed"
        if name == "approve":
            dec = approval.get("decision")
            return "success" if dec == "approved" else ("hold" if dec == "hold" else ("rejected" if dec == "rejected" else ("pending" if validation else "skipped")))
        if name == "pay":
            if payment and payment.get("status") == "success":
                return "success"
            if approval and approval.get("decision") == "approved" and not payment:
                return "pending"
            if payment and payment.get("status") == "failed":
                return "error"
            return "skipped"
        return "unknown"

    timeline = [
        {"step": "ingest", "status": step_status("ingest")},
        {"step": "validate", "status": step_status("validate")},
        {"step": "approve", "status": step_status("approve")},
        {"step": "pay", "status": step_status("pay")},
    ]

    # plain-language explanation
    explanation: List[str] = []
    if result.get("error"):
        explanation.append(f"Processing error: {result.get('error')}")
    else:
        dec = approval.get("decision")
        if dec == "approved":
            if payment and payment.get("status") == "success":
                explanation.append("This invoice was approved and paid.")
            else:
                explanation.append("This invoice is approved and ready for payment.")
        elif dec == "hold":
            hints = approval.get("correction_hints") or []
            if hints:
                explanation.append("Invoice is on hold. Suggested corrections: " + ", ".join(map(str, hints)))
            else:
                explanation.append("Invoice is on hold and requires human review.")
        elif dec == "rejected":
            explanation.append("Invoice was rejected. See approval reason for details.")
        else:
            if not inv:
                explanation.append("No invoice could be extracted from the file.")
            else:
                explanation.append("No approval decision available. Review the validation results.")

    raw = {
        "extracted_invoice": inv,
        "validation_result": validation,
        "approval_decision": approval,
        "payment_result": payment,
        "trace_log": result.get('trace_log', []),
    }

    return {
        "header": header,
        "cards": cards,
        "timeline": timeline,
        "explanation": explanation,
        "raw": raw,
    }
