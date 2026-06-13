"""LangGraph state machine definition for the invoice processing pipeline.

Defines nodes, edges, and conditional routing for the four-agent workflow.
"""

from __future__ import annotations

from typing import Any, Literal

from langgraph.graph import END, StateGraph

from agents.approval_agent import MAX_CORRECTION_LOOPS, approve_node
from agents.ingest_agent import ingest_node
from agents.payment_agent import payment_node
from agents.validate_agent import validate_node
from .state import InvoiceState
from .observability import get_logger, invoices_processed_total, invoices_failed_total, stage_duration_seconds
import time
from .audit import record_pipeline_summary, record_pipeline_error


def _has_error(state: dict) -> bool:
    """Check if the current state has a non-None error."""
    return state.get("error") is not None


def _has_extracted_invoice(state: dict) -> bool:
    """Check if extraction succeeded."""
    return state.get("extracted_invoice") is not None


def _has_validation(state: dict) -> bool:
    """Check if validation completed."""
    return state.get("validation_result") is not None


def _get_approval_decision(state: dict) -> str | None:
    """Get the approval decision string."""
    decision = state.get("approval_decision")
    if decision and isinstance(decision, dict):
        return decision.get("decision")
    return None


def _get_correction_count(state: dict) -> int:
    """Get the current correction loop count."""
    decision = state.get("approval_decision")
    if decision and isinstance(decision, dict):
        return int(decision.get("correction_count", 0) or 0)
    return 0


def _get_correction_hints(state: dict) -> list[str]:
    """Get correction hints produced by approval."""
    decision = state.get("approval_decision")
    if decision and isinstance(decision, dict):
        hints = decision.get("correction_hints", [])
        if isinstance(hints, list):
            return [str(h).strip() for h in hints if str(h).strip()]
    return []


def should_reingest(state: dict) -> Literal["ingest", "payment", "__end__"]:
    """Router: after approval, decide next step.

    - If there's an error at any stage, end.
    - If approval is "hold" and correction hints exist, re-ingest up to the
      configured loop cap.
    - If approval is "approved", proceed to payment.
    - If approval is "rejected", skip to payment (which logs rejection).
    - Otherwise, end.
    """
    if _has_error(state):
        return END

    decision = _get_approval_decision(state)
    if decision is None:
        return END

    if decision == "hold":
        hints = _get_correction_hints(state)
        if hints and _get_correction_count(state) < MAX_CORRECTION_LOOPS:
            print(
                f"  [ROUTER] Correction loop {_get_correction_count(state) + 1}/"
                f"{MAX_CORRECTION_LOOPS} - re-ingesting"
            )
            return "ingest"

    return "payment"


def after_ingest(state: dict) -> Literal["validate", "__end__"]:
    """Router: after ingestion, validate or end on error."""
    if _has_error(state) or not _has_extracted_invoice(state):
        return END
    return "validate"


def after_validate(state: dict) -> Literal["approve", "__end__"]:
    """Router: after validation, approve or end on error."""
    if _has_error(state) or not _has_validation(state):
        return END
    return "approve"


def after_payment(state: dict) -> Literal["__end__"]:
    """Router: after payment, always end."""
    return END


def build_graph() -> StateGraph:
    """Build and compile the LangGraph state machine."""
    graph = StateGraph(InvoiceState)

    graph.add_node("ingest", ingest_node)
    graph.add_node("validate", validate_node)
    graph.add_node("approve", approve_node)
    graph.add_node("payment", payment_node)

    graph.set_entry_point("ingest")

    graph.add_conditional_edges("ingest", after_ingest)
    graph.add_conditional_edges("validate", after_validate)
    graph.add_conditional_edges("approve", should_reingest)
    graph.add_conditional_edges("payment", after_payment)

    return graph.compile()


def run_pipeline(file_path: str) -> dict[str, Any]:
    """Run the full pipeline for a single invoice file and emit metrics."""
    graph = build_graph()

    logger = get_logger(__name__)

    initial_state = InvoiceState(
        file_path=file_path,
        raw_text="",
        extracted_invoice=None,
        validation_result=None,
        approval_decision=None,
        payment_result=None,
        error=None,
        trace_log=[],
    )

    start = time.time()
    result = graph.invoke(initial_state)
    duration = time.time() - start

    # Record metrics where available
    try:
        if stage_duration_seconds is not None:
            stage_duration_seconds.observe(duration)
        if result.get("error"):
            if invoices_failed_total is not None:
                invoices_failed_total.inc()
            logger.error("invoice processing failed", extra={"file": file_path, "error": result.get("error")})
        else:
            if invoices_processed_total is not None:
                invoices_processed_total.inc()
            logger.info("invoice processed", extra={"file": file_path, "duration": duration})
    except Exception:
        logger.exception("failed to record metrics")

    # Best-effort: record pipeline summary audit event
    try:
        result["file_path"] = file_path
        record_pipeline_summary(result)
    except Exception:
        try:
            # If something blew up and we have an invoice id, record the error
            invoice = result.get("extracted_invoice") or {}
            inv = invoice.get("invoice_number") if isinstance(invoice, dict) else ""
            record_pipeline_error(inv or "", file_path, "pipeline", result.get("error") or "")
        except Exception:
            logger.exception("failed to record pipeline audit summary")

    return result


def print_summary(result: dict[str, Any]) -> None:
    """Print a human-readable summary of the pipeline results."""
    import sys

    print("\n" + "=" * 60)
    print("  INVOICE PROCESSING SUMMARY")
    print("=" * 60)

    invoice = result.get("extracted_invoice")
    validation = result.get("validation_result")
    approval = result.get("approval_decision")
    payment = result.get("payment_result")

    if result.get("error"):
        print(f"\n  [ERROR]: {result['error']}")
        return

    if invoice:
        print(f"\n  [INVOICE] Number:    {invoice.get('invoice_number', 'N/A')}")
        print(f"  [INVOICE] Vendor:    {invoice.get('vendor', 'N/A')}")
        print(f"  [INVOICE] Total:     {invoice.get('total', 0):,.2f} {invoice.get('currency', 'USD')}")
        print(f"  [INVOICE] Date:      {invoice.get('date', 'N/A')}")
        print(f"  [INVOICE] Due Date:  {invoice.get('due_date', 'N/A')}")

    def _safe_str(s: str | None) -> str | None:
        if s is None:
            return s
        enc = sys.stdout.encoding or "utf-8"
        try:
            return s.encode(enc, errors="replace").decode(enc)
        except Exception:
            return s

    if validation:
        status = "[PASS]" if validation.get("passed") else "[FAIL]"
        print(f"\n  [VALIDATION] Status: {status}")
        if validation.get("summary"):
            print(f"  [VALIDATION] Info:   {_safe_str(validation['summary'])}")
    else:
        print("\n  [VALIDATION] Status: Not run")

    if approval:
        print(f"\n  [APPROVAL] Decision: {approval.get('decision', 'N/A').upper()}")
        print(f"  [APPROVAL] Reason:   {_safe_str(approval.get('reason', 'N/A'))}")
    else:
        print("\n  [APPROVAL] Decision: Not run")

    if payment:
        status = payment.get("status", "N/A").upper()
        print(f"\n  [PAYMENT] Status:    {status}")
        if payment.get("tx_id"):
            print(f"  [PAYMENT] TX ID:     {payment['tx_id']}")
        if payment.get("error"):
            print(f"  [PAYMENT] Error:     {_safe_str(payment['error'])}")
    else:
        print("\n  [PAYMENT] Status:    Not run")

    print("=" * 60 + "\n")
