"""Stage 2: deterministic validation of extracted invoice data against inventory.

No LLM. Checks: revision/duplicate detection, stock levels (including
cumulative across approved invoices), total arithmetic, unit price deviation,
and foreign currency. Also exposes check_inventory as a LangChain @tool for
the approval agent to call during its reasoning pass.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from langchain_core.tools import tool

from config import PRICE_MISMATCH_TOLERANCE, TOTAL_MISMATCH_TOLERANCE
from src.db.queries import get_original_invoice_amount
from src.graph.state import InvoiceState, ValidationFlag
from src.ops_db import (
    ApprovedQuantity,
    SessionLocal,
    check_fingerprint,
    compute_fingerprint,
    get_cumulative_approved_qty,
    get_item,
)

logger = logging.getLogger(__name__)


# ── LangChain tool exposed to the approval agent ─────────────────────────────


@tool
def check_inventory(item: str, qty: int = 0) -> dict:
    """Look up inventory for an item. If qty > 0, also returns whether stock is sufficient.

    Returns: item, found, stock, unit_price, category, and (if qty > 0) sufficient_stock.
    """
    inv = get_item(item)
    if inv is None:
        return {
            "item": item,
            "found": False,
            "stock": 0,
            "unit_price": None,
            "category": None,
        }
    result = {
        "item": item,
        "found": True,
        "stock": inv.stock,
        "unit_price": inv.unit_price,
        "category": inv.category,
    }
    if qty > 0:
        result["sufficient_stock"] = inv.stock >= qty
    return result


# ── Internal helpers ──────────────────────────────────────────────────────────


def _group_items(items: list[dict]) -> dict[str, int]:
    """Sum quantities for items that appear multiple times on the same invoice."""
    grouped: dict[str, int] = {}
    for item in items:
        name = item.get("name", "")
        qty = item.get("qty", 0)
        if not isinstance(qty, int) or qty <= 0:
            continue
        grouped[name] = grouped.get(name, 0) + qty
    return grouped


def _compute_expected_total(
    items: list[dict], tax_amount: float | None
) -> float | None:
    """Compute the expected grand total from line items and tax."""
    subtotal = 0.0
    for item in items:
        qty = item.get("qty")
        unit_price = item.get("unit_price")
        if qty is None or unit_price is None:
            return None  # can't compute without full data
        if not isinstance(qty, int) or qty <= 0:
            continue
        subtotal += qty * unit_price
    return subtotal + (tax_amount or 0.0)


def _is_revision(invoice_path: str, extracted_data: dict) -> bool:
    """Return True if this invoice is a deliberate revision, not a duplicate."""
    basename = os.path.basename(invoice_path)
    if "_revised" in basename.lower():
        return True
    # json_parser emits "[NOTE: this is a revised invoice]" into the raw text;
    # the LLM surfaces it in extraction_warnings.
    warnings = extracted_data.get("extraction_warnings", [])
    return any("revised invoice" in w.lower() for w in warnings)


# ── Main validation logic ─────────────────────────────────────────────────────


def run_validation(state: InvoiceState) -> dict[str, Any]:
    """LangGraph node: validate extracted invoice data against inventory.

    Args:
        state: Pipeline state containing ``extracted_data`` and
               ``invoice_path``.

    Returns:
        Partial state dict with ``validation_flags`` and ``is_duplicate``.
    """
    extracted_data: dict = state.get("extracted_data", {})
    invoice_path: str = state.get("invoice_path", "")
    invoice_number: str = extracted_data.get("invoice_number") or ""
    items: list[dict] = extracted_data.get("items", [])
    stated_total: float | None = extracted_data.get("amount")
    tax_amount: float | None = extracted_data.get("tax_amount")
    currency: str = extracted_data.get("currency") or "USD"

    flags: list[dict] = []
    is_duplicate = False
    original_qty_map: dict[
        str, int
    ] = {}  # items already fulfilled from the original invoice
    revision_delta_amount: float | None = None

    logger.info(
        "Validation: %s (currency=%s, items=%d)", invoice_number, currency, len(items)
    )

    # ── Check 1: Foreign currency ─────────────────────────────────────────────
    if currency.upper() != "USD":
        flags.append(
            ValidationFlag(
                item="invoice",
                issue_type="foreign_currency",
                detail=(
                    f"Invoice is denominated in {currency}, not USD. "
                    "Price and budget checks cannot be performed without an FX rate. "
                    "Stock checks will still run; this invoice routes to needs_review."
                ),
            ).model_dump()
        )
        logger.info("Validation: foreign currency detected (%s)", currency)

    is_revision = _is_revision(invoice_path, extracted_data)

    # ── Check 2: Revision detection ───────────────────────────────────────────
    if is_revision:
        flags.append(
            ValidationFlag(
                item=invoice_number or "unknown",
                issue_type="revision_detected",
                detail=(
                    "This invoice is a revision of a previously issued invoice "
                    "with the same invoice number. Processing the revised version "
                    "as authoritative. Not treated as a duplicate."
                ),
            ).model_dump()
        )
        logger.info("Validation: revision detected for %s", invoice_number)

        # If the original was already paid, compute a delta and pay only the difference.
        # Build original_qty_map so the stock check doesn't double-count fulfilled items.
        if invoice_number:
            with SessionLocal() as session:
                orig_rows = (
                    session.query(ApprovedQuantity)
                    .filter(ApprovedQuantity.invoice_number == invoice_number)
                    .all()
                )
            if orig_rows:
                original_qty_map = {r.item: r.quantity for r in orig_rows}
                original_amount = get_original_invoice_amount(invoice_number)
                revised_amount = extracted_data.get("amount") or 0.0
                if original_amount is not None:
                    revision_delta_amount = round(
                        max(0.0, revised_amount - original_amount), 2
                    )
                    delta_note = f"Delta to pay: ${revision_delta_amount:,.2f} (revised ${revised_amount:,.2f} - original ${original_amount:,.2f})."
                else:
                    revision_delta_amount = revised_amount
                    delta_note = (
                        "Original paid amount not found; full revised amount queued."
                    )
                flags.append(
                    ValidationFlag(
                        item=invoice_number,
                        issue_type="revision_of_paid_invoice",
                        detail=(
                            f"Original {invoice_number} was already paid. "
                            f"{delta_note} "
                            "Stock checks exclude already-fulfilled items."
                        ),
                    ).model_dump()
                )
                logger.info(
                    "Validation: revision of paid invoice %s — delta=%.2f",
                    invoice_number,
                    revision_delta_amount or 0.0,
                )

    # ── Check 3: Duplicate detection ─────────────────────────────────────────
    # Two independent signals: invoice-number match (fast) and SHA-256 content
    # fingerprint of (vendor + amount + due_date) — catches same economic
    # transaction resubmitted with a different invoice number.
    amount_for_fp: float = extracted_data.get("amount") or 0.0
    due_date_for_fp: str = extracted_data.get("due_date") or ""
    vendor_for_fp: str = extracted_data.get("vendor") or ""
    fingerprint = compute_fingerprint(vendor_for_fp, amount_for_fp, due_date_for_fp)

    if not is_revision:
        # Check 3a: invoice-number duplicate
        if invoice_number:
            with SessionLocal() as session:
                already_approved = (
                    session.query(ApprovedQuantity)
                    .filter(ApprovedQuantity.invoice_number == invoice_number)
                    .first()
                )
            if already_approved:
                is_duplicate = True
                flags.append(
                    ValidationFlag(
                        item=invoice_number,
                        issue_type="duplicate_invoice",
                        detail=(
                            f"Invoice {invoice_number} has already been approved and recorded "
                            "(invoice-number match). Rejecting to prevent double payment."
                        ),
                    ).model_dump()
                )
                logger.warning(
                    "Validation: DUPLICATE invoice %s (number match)", invoice_number
                )

        # Check 3b: content-fingerprint duplicate (catches re-numbered resubmissions)
        if not is_duplicate and vendor_for_fp and check_fingerprint(fingerprint):
            is_duplicate = True
            flags.append(
                ValidationFlag(
                    item=invoice_number or "unknown",
                    issue_type="duplicate_invoice",
                    detail=(
                        "Content fingerprint (vendor + amount + due date) matches a previously "
                        "paid invoice. Rejecting to prevent double payment even though the "
                        "invoice number differs."
                    ),
                ).model_dump()
            )
            logger.warning(
                "Validation: DUPLICATE invoice %s (fingerprint match)", invoice_number
            )

    # ── Checks 4–7: Per-item validation ───────────────────────────────────────
    grouped = _group_items(items)

    for item_name, total_requested_qty in grouped.items():
        inv = get_item(item_name)

        # Unknown item
        if inv is None:
            flags.append(
                ValidationFlag(
                    item=item_name,
                    issue_type="unknown_item",
                    detail=f"'{item_name}' is not found in the inventory database.",
                ).model_dump()
            )
            logger.info("Validation: unknown item '%s'", item_name)
            continue

        # Zero stock
        if inv.stock == 0:
            flags.append(
                ValidationFlag(
                    item=item_name,
                    issue_type="out_of_stock",
                    detail=f"'{item_name}' has 0 units in stock.",
                ).model_dump()
            )
            logger.info("Validation: out-of-stock '%s'", item_name)
            continue

        # Cumulative stock check — add previously-approved quantities.
        # For revisions: subtract the original invoice's contribution so already-fulfilled
        # items don't get double-counted against available stock.
        approved_elsewhere = get_cumulative_approved_qty(item_name)
        already_fulfilled = original_qty_map.get(item_name, 0)
        net_approved = approved_elsewhere - already_fulfilled
        combined = net_approved + total_requested_qty
        if combined > inv.stock:
            flags.append(
                ValidationFlag(
                    item=item_name,
                    issue_type="insufficient_stock",
                    detail=(
                        f"'{item_name}': requested {total_requested_qty} units"
                        + (
                            f" (plus {net_approved} already approved "
                            f"= {combined} combined)"
                            if net_approved
                            else ""
                        )
                        + f", only {inv.stock} in stock."
                    ),
                ).model_dump()
            )
            logger.info(
                "Validation: insufficient stock for '%s' (need %d, have %d)",
                item_name,
                combined,
                inv.stock,
            )

        # Price mismatch check — skipped for non-USD (no FX rate).
        # Use the first line-item price found for this item to avoid raising
        # the same flag multiple times when an item appears on several lines.
        if inv.unit_price and currency.upper() == "USD":
            invoiced_price = next(
                (
                    r.get("unit_price")
                    for r in items
                    if r.get("name") == item_name and r.get("unit_price") is not None
                ),
                None,
            )
            if invoiced_price is not None:
                deviation = abs(invoiced_price - inv.unit_price) / inv.unit_price
                if deviation > PRICE_MISMATCH_TOLERANCE:
                    flags.append(
                        ValidationFlag(
                            item=item_name,
                            issue_type="price_mismatch",
                            detail=(
                                f"'{item_name}': invoiced at ${invoiced_price:.2f}/unit "
                                f"but expected ${inv.unit_price:.2f}/unit "
                                f"({deviation:.0%} deviation, threshold {PRICE_MISMATCH_TOLERANCE:.0%})."
                            ),
                        ).model_dump()
                    )
                    logger.info(
                        "Validation: price mismatch for '%s' (%.2f vs %.2f)",
                        item_name,
                        invoiced_price,
                        inv.unit_price,
                    )

    # ── Check 8: Invalid quantity (negative/non-numeric/zero) ─────────────────
    for raw_item in items:
        qty = raw_item.get("qty")
        name = raw_item.get("name", "?")
        if qty is None or not isinstance(qty, int) or qty <= 0:
            flags.append(
                ValidationFlag(
                    item=name,
                    issue_type="invalid_quantity",
                    detail=f"'{name}': invalid quantity '{qty}' (must be a positive integer).",
                ).model_dump()
            )
            logger.info("Validation: invalid quantity for '%s': %s", name, qty)

    # ── Check 9: Total mismatch ───────────────────────────────────────────────
    # Only check if we're in USD and have enough data to compute.
    if stated_total is not None and currency.upper() == "USD":
        expected_total = _compute_expected_total(items, tax_amount)
        if expected_total is not None:
            discrepancy = abs(stated_total - expected_total)
            if discrepancy > TOTAL_MISMATCH_TOLERANCE:
                flags.append(
                    ValidationFlag(
                        item="invoice_total",
                        issue_type="total_mismatch",
                        detail=(
                            f"Stated total ${stated_total:,.2f} does not match "
                            f"computed total ${expected_total:,.2f} "
                            f"(discrepancy: ${discrepancy:,.2f})."
                        ),
                    ).model_dump()
                )
                logger.info(
                    "Validation: total mismatch — stated %.2f vs computed %.2f",
                    stated_total,
                    expected_total,
                )

    logger.info(
        "Validation complete: %d flag(s), is_duplicate=%s", len(flags), is_duplicate
    )
    severities = [f.get("severity", "warning") for f in flags]
    note = (
        f"{len(flags)} flag(s): " + ", ".join(f["issue_type"] for f in flags)
        if flags
        else "clean"
    )
    result: dict[str, Any] = {
        "validation_flags": flags,
        "is_duplicate": is_duplicate,
        "audit_log": [
            {
                "stage": "validation",
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "error"
                if any(s == "error" for s in severities)
                else ("warning" if flags else "ok"),
                "note": note,
            }
        ],
    }
    if revision_delta_amount is not None:
        result["revision_delta_amount"] = revision_delta_amount
    return result
