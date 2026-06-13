"""Validation Agent — deterministic checks against inventory database.

This agent NEVER uses the LLM. It queries SQLite directly and applies
deterministic rules to flag stock mismatches, unknown items, and data
integrity issues.
"""

from __future__ import annotations

from collections import Counter

from src.database import query_inventory
from src.state import (
    ExtractedInvoice,
    InventoryCheck,
    IntegrityCheck,
    ValidationResult,
)
from src.tools import write_trace, get_error_context
import src.config as config
from src.audit import record_stage_event
from pathlib import Path


def validate_node(state: dict) -> dict:
    """LangGraph node: validate extracted invoice data against inventory.

    Deterministic checks:
    1. Inventory checks — each item queried against DB
    2. Data integrity checks — negative quantities, missing fields
    3. Aggregate duplicate items before stock validation
    """
    extracted_data = state.get("extracted_invoice")
    if not extracted_data:
        state["error"] = "No extracted invoice data to validate"
        return state

    invoice = ExtractedInvoice(**extracted_data)
    checks: list[InventoryCheck] = []
    integrity_errors: list[IntegrityCheck] = []

    # --- Step 1: Aggregate quantities for duplicate items ---
    item_qty_counter: Counter = Counter()
    for li in invoice.items:
        item_qty_counter[li.item] += li.qty

    # --- Step 2: Check each item against inventory ---
    for item_name, total_qty in item_qty_counter.items():
        inv_record = query_inventory(item_name)

        if inv_record is None:
            checks.append(InventoryCheck(
                item=item_name,
                requested_qty=total_qty,
                status="unknown_item",
                message=f"Item '{item_name}' not found in inventory database",
            ))
        elif inv_record["stock"] == 0:
            checks.append(InventoryCheck(
                item=item_name,
                requested_qty=total_qty,
                available_stock=0,
                status="out_of_stock",
                message=f"Item '{item_name}' has zero stock — possible fraudulent entry",
            ))
        elif total_qty > inv_record["stock"]:
            checks.append(InventoryCheck(
                item=item_name,
                requested_qty=total_qty,
                available_stock=inv_record["stock"],
                status="stock_mismatch",
                message=f"Item '{item_name}': requested {total_qty}, only {inv_record['stock']} in stock",
            ))
        else:
            checks.append(InventoryCheck(
                item=item_name,
                requested_qty=total_qty,
                available_stock=inv_record["stock"],
                status="ok",
                message=f"Item '{item_name}': {total_qty} available ({inv_record['stock']} in stock)",
            ))

    # --- Step 3: Data integrity checks ---
    # Check negative quantities
    for li in invoice.items:
        if li.qty < 0:
            integrity_errors.append(IntegrityCheck(
                field=f"items[{li.item}].qty",
                issue=f"Negative quantity ({li.qty}) for item '{li.item}'",
                severity="error",
            ))

    # Check negative total
    if invoice.total < 0:
        integrity_errors.append(IntegrityCheck(
            field="total",
            issue=f"Negative total amount ({invoice.total})",
            severity="error",
        ))

    # Check empty vendor
    if not invoice.vendor or invoice.vendor == "Unknown Vendor":
        integrity_errors.append(IntegrityCheck(
            field="vendor",
            issue="Missing or unknown vendor name",
            severity="warning",
        ))

    # Check missing due date
    if not invoice.due_date:
        integrity_errors.append(IntegrityCheck(
            field="due_date",
            issue="Missing due date",
            severity="warning",
        ))

    # Check for null/empty invoice number
    if not invoice.invoice_number or invoice.invoice_number == "unknown":
        integrity_errors.append(IntegrityCheck(
            field="invoice_number",
            issue="Missing or unparseable invoice number",
            severity="error",
        ))

    # --- Step 4: Determine overall pass/fail ---
    failed_checks = [c for c in checks if c.status != "ok"]
    critical_errors = [e for e in integrity_errors if e.severity == "error"]
    passed = len(failed_checks) == 0 and len(critical_errors) == 0

    # Build summary
    summary_parts = []
    if checks:
        ok_count = sum(1 for c in checks if c.status == "ok")
        total_count = len(checks)
        summary_parts.append(f"Inventory: {ok_count}/{total_count} checks passed")

    if failed_checks:
        for c in failed_checks:
            summary_parts.append(f"  ⚠ {c.message}")

    if integrity_errors:
        for e in integrity_errors:
            summary_parts.append(f"  {'❌' if e.severity == 'error' else '⚠'} {e.field}: {e.issue}")

    result = ValidationResult(
        invoice_number=invoice.invoice_number,
        inventory_checks=checks,
        integrity_errors=integrity_errors,
        passed=passed,
        summary="\n".join(summary_parts) if summary_parts else "All checks passed",
    )

    state["validation_result"] = result.model_dump()
    write_trace(state, "validate_agent")

    try:
        file_path = state.get("file_path", "")
        file_name = Path(file_path).name if file_path else ""
        record_stage_event(
            invoice_number=invoice.invoice_number,
            file_name=file_name,
            stage="validate",
            status=("passed" if result.passed else "failed"),
            reason=(result.summary or ""),
            actor="validate_agent",
            metadata={"checks_count": len(result.inventory_checks)},
            total=invoice.total,
            vendor=invoice.vendor or "",
        )
    except Exception:
        pass

    status_icon = "✓" if passed else "✗"
    print(f"  [VALIDATE] {invoice.invoice_number} | {'PASS' if passed else 'FAIL'} | "
          f"{len(failed_checks)} inventory issues, {len(critical_errors)} integrity errors")

    if config.VERBOSE:
        if checks:
            print(f"    [VALIDATE DETAIL] Inventory checks: {[c.model_dump() if hasattr(c, 'model_dump') else c for c in checks]}")
        if integrity_errors:
            print(f"    [VALIDATE DETAIL] Integrity errors: {[e.model_dump() if hasattr(e, 'model_dump') else e for e in integrity_errors]}")

    return state
