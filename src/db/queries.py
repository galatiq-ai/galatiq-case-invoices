"""Write helpers for the normalized schema.

All functions are idempotent where reasonable and safe to call from
multiple pipeline nodes without worrying about ordering or partial writes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func

from src.db.models import (
    AuditEvent,
    CatalogItem,
    Invoice,
    InvoiceLineItem,
    InvoiceValidationFlag,
    Vendor,
    create_all,
)
from src.db.seed import seed_catalog, seed_vendors
from src.db.session import NSession

logger = logging.getLogger(__name__)


def init_normalized_db() -> None:
    """Bootstrap the normalized schema: create tables then seed reference data."""
    create_all()
    seed_vendors()
    seed_catalog()
    logger.debug("Normalized DB initialised")


def upsert_vendor(name: str, category: str | None = None) -> int:
    """Return the vendor id for *name*, inserting a new row if necessary."""
    with NSession() as session:
        vendor = session.query(Vendor).filter_by(name=name).first()
        if vendor is None:
            vendor = Vendor(
                name=name,
                category=category,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(vendor)
            session.flush()
        vid = vendor.id
        session.commit()
    return vid


def _resolve_catalog_id(session: Any, item_name: str) -> int | None:
    """Return the catalog_items.id for *item_name*, or None if not found."""
    row = session.query(CatalogItem).filter_by(name=item_name).first()
    return row.id if row else None


def record_invoice_run(state: dict[str, Any]) -> None:
    """Persist the complete result of one pipeline run to the normalized schema.

    Creates or updates rows in:
        invoices, invoice_line_items, invoice_validation_flags, audit_events

    Safe to call after the pipeline completes (payment node).  If the invoice
    run_id already exists (idempotent re-run), the call is a no-op to avoid
    duplicates — pipeline retries won't double-record.
    """
    run_id: str = state.get("run_id", "unknown")
    extracted: dict = state.get("extracted_data") or {}
    flags: list[dict] = state.get("validation_flags") or []
    audit_log: list[dict] = state.get("audit_log") or []
    status: str = state.get("final_decision") or "unknown"
    now = datetime.now(timezone.utc).isoformat()

    vendor_name: str = extracted.get("vendor") or ""
    amount: float = extracted.get("amount") or 0.0
    paid_amount: float | None = state.get("revision_delta_amount")
    currency: str = extracted.get("currency") or "USD"
    due_date: str | None = extracted.get("due_date")
    invoice_number: str | None = extracted.get("invoice_number")
    invoice_path: str | None = state.get("invoice_path")
    decision_reasoning: str | None = state.get("decision_reasoning")

    from src.ops_db import compute_fingerprint

    fingerprint = compute_fingerprint(vendor_name, amount, due_date or "")

    try:
        with NSession() as session:
            # Idempotency guard
            existing = session.query(Invoice).filter_by(run_id=run_id).first()
            if existing:
                logger.debug(
                    "record_invoice_run: run_id %s already persisted, skipping", run_id
                )
                return

            # Resolve or create vendor
            vendor_id: int | None = None
            if vendor_name:
                vendor = session.query(Vendor).filter_by(name=vendor_name).first()
                if vendor is None:
                    vendor = Vendor(name=vendor_name, category=None, created_at=now)
                    session.add(vendor)
                    session.flush()
                vendor_id = vendor.id

            # Insert invoice header
            invoice = Invoice(
                run_id=run_id,
                invoice_number=invoice_number,
                vendor_id=vendor_id,
                amount=amount,
                paid_amount=paid_amount,
                currency=currency,
                due_date=due_date,
                status=status,
                decision_reasoning=decision_reasoning,
                fingerprint=fingerprint,
                invoice_path=invoice_path,
                created_at=now,
            )
            session.add(invoice)
            session.flush()  # invoice.id is now available

            # Insert line items
            for item in extracted.get("items") or []:
                name = item.get("name") or ""
                qty = item.get("qty")
                price = item.get("unit_price")
                total = (qty * price) if (qty and price) else None
                cat_id = _resolve_catalog_id(session, name)
                session.add(
                    InvoiceLineItem(
                        invoice_id=invoice.id,
                        catalog_item_id=cat_id,
                        item_name=name,
                        quantity=qty,
                        unit_price=price,
                        line_total=total,
                    )
                )

            # Insert validation flags
            for flag in flags:
                session.add(
                    InvoiceValidationFlag(
                        invoice_id=invoice.id,
                        issue_type=flag.get("issue_type", ""),
                        severity=flag.get("severity", "warning"),
                        item_name=flag.get("item"),
                        detail=flag.get("detail", ""),
                        created_at=now,
                    )
                )

            # Insert audit events
            for entry in audit_log:
                session.add(
                    AuditEvent(
                        invoice_id=invoice.id,
                        run_id=run_id,
                        stage=entry.get("stage", ""),
                        status=entry.get("status", "ok"),
                        note=entry.get("note"),
                        created_at=entry.get("ts", now),
                    )
                )

            session.commit()
            logger.info(
                "Normalized DB: recorded run_id=%s status=%s vendor=%s amount=%.2f",
                run_id,
                status,
                vendor_name,
                amount,
            )
    except Exception as exc:
        logger.error("record_invoice_run failed (non-fatal): %s", exc)


def get_all_invoice_runs() -> list[dict]:
    """Return all invoice runs from the normalized DB, newest first."""
    try:
        with NSession() as session:
            rows = (
                session.query(Invoice, Vendor)
                .outerjoin(Vendor, Invoice.vendor_id == Vendor.id)
                .order_by(Invoice.created_at.desc())
                .all()
            )
            return [
                {
                    "Invoice": r.Invoice.invoice_number or "?",
                    "Vendor": (r.Vendor.name if r.Vendor else "?")[:30],
                    "Amount": float(
                        r.Invoice.paid_amount
                        if r.Invoice.paid_amount is not None
                        else r.Invoice.amount or 0
                    ),
                    "InvoiceTotal": float(r.Invoice.amount or 0),
                    "IsRevision": r.Invoice.paid_amount is not None,
                    "Currency": r.Invoice.currency or "USD",
                    "Status": r.Invoice.status or "unknown",
                    "CreatedAt": (r.Invoice.created_at or "")[:10],
                    "RunId": r.Invoice.run_id or "",
                }
                for r in rows
            ]
    except Exception as exc:
        logger.warning("get_all_invoice_runs failed (non-fatal): %s", exc)
        return []


def get_original_invoice_amount(invoice_number: str) -> float | None:
    """Return the total amount paid for a previously approved invoice, or None."""
    if not invoice_number:
        return None
    try:
        with NSession() as session:
            row = (
                session.query(Invoice)
                .filter(
                    Invoice.invoice_number == invoice_number,
                    Invoice.status == "approved",
                )
                .order_by(Invoice.created_at.desc())
                .first()
            )
            return float(row.amount) if row and row.amount is not None else None
    except Exception as exc:
        logger.warning("get_original_invoice_amount failed (non-fatal): %s", exc)
        return None


def get_vendor_risk_profile(vendor_name: str) -> dict:
    """Query the normalized DB for this vendor's full submission history.

    Returns a dict with known_vendor, submission counts, approval/rejection
    rates, and the most frequent flag types raised against this vendor.
    Safe to call on an empty DB — returns a 'new vendor' profile.
    """
    if not vendor_name or not vendor_name.strip():
        return _unknown_vendor_profile()
    try:
        with NSession() as session:
            vendor = (
                session.query(Vendor)
                .filter(func.lower(Vendor.name) == vendor_name.strip().lower())
                .first()
            )
            if not vendor:
                return _unknown_vendor_profile()

            stats = (
                session.query(
                    func.count(Invoice.id).label("total"),
                    func.sum(case((Invoice.status == "approved", 1), else_=0)).label(
                        "approved"
                    ),
                    func.sum(case((Invoice.status == "rejected", 1), else_=0)).label(
                        "rejected"
                    ),
                )
                .filter(Invoice.vendor_id == vendor.id)
                .one()
            )

            total = stats.total or 0
            approved = stats.approved or 0
            rejected = stats.rejected or 0

            flag_rows = (
                session.query(
                    InvoiceValidationFlag.issue_type,
                    func.count(InvoiceValidationFlag.id).label("cnt"),
                )
                .join(Invoice, InvoiceValidationFlag.invoice_id == Invoice.id)
                .filter(
                    Invoice.vendor_id == vendor.id,
                )
                .group_by(InvoiceValidationFlag.issue_type)
                .order_by(func.count(InvoiceValidationFlag.id).desc())
                .limit(5)
                .all()
            )

            last = (
                session.query(Invoice)
                .filter(Invoice.vendor_id == vendor.id)
                .order_by(Invoice.created_at.desc())
                .first()
            )

            return {
                "known_vendor": total > 0,
                "total_submissions": total,
                "approved": approved,
                "rejected": rejected,
                "approval_rate": round(approved / total, 2) if total else 0.0,
                "rejection_rate": round(rejected / total, 2) if total else 0.0,
                "common_flags": [(r.issue_type, r.cnt) for r in flag_rows],
                "last_decision": last.status if last else None,
                "vendor_category": vendor.category,
            }
    except Exception as exc:
        logger.warning("get_vendor_risk_profile failed (non-fatal): %s", exc)
        return _unknown_vendor_profile()


def _unknown_vendor_profile() -> dict:
    return {
        "known_vendor": False,
        "total_submissions": 0,
        "approved": 0,
        "rejected": 0,
        "approval_rate": 0.0,
        "rejection_rate": 0.0,
        "common_flags": [],
        "last_decision": None,
        "vendor_category": None,
    }
