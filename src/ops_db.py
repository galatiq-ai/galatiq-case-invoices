"""Operational database: schema, seed data, and query helpers.

All three operational tables live in acme.db (shared with the normalized
analytics schema in src/db/ and the precedent store in precedent_db.py).

Tables:
  inventory            — items, stock levels, expected unit prices, category
  approved_quantities  — cumulative record of quantities committed by approved
                         invoices (enables cross-invoice stock enforcement in
                         batch mode)
  invoice_fingerprints — SHA-256 content fingerprints for tamper-resistant
                         duplicate detection independent of invoice numbers
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    create_engine,
    event,
    func,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import DB_PATH

logger = logging.getLogger(__name__)

_engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
SessionLocal = sessionmaker(bind=_engine)


@event.listens_for(_engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record) -> None:
    """Enable WAL mode and a busy-timeout on every new connection.

    WAL (Write-Ahead Logging) allows concurrent reads during writes and
    eliminates the page-lock contention that caused Payment node latency.
    busy_timeout makes writers retry for up to 5 s before raising an error.
    """
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(DeclarativeBase):
    pass


class InventoryItem(Base):
    __tablename__ = "inventory"

    item = Column(String, primary_key=True)
    stock = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=True)  # expected USD unit price
    category = Column(String, nullable=True)


class ApprovedQuantity(Base):
    __tablename__ = "approved_quantities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    invoice_number = Column(String, nullable=False, index=True)
    item = Column(String, nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    approved_at = Column(DateTime, nullable=False)


class InvoiceFingerprint(Base):
    """Content-based deduplication independent of invoice number format.

    SHA-256 of (vendor + amount + due_date) catches same-invoice resubmissions
    even when the invoice number differs between submissions — a common fraud
    pattern that invoice-number-only checks miss.
    """

    __tablename__ = "invoice_fingerprints"

    fingerprint = Column(String, primary_key=True)
    invoice_number = Column(String, nullable=True)
    vendor = Column(String, nullable=True)
    created_at = Column(String, nullable=False)


# Seed inventory: unit_price and category are extended fields beyond stock-only.
_SEED_ITEMS = [
    InventoryItem(item="WidgetA", stock=15, unit_price=250.00, category="Widget"),
    InventoryItem(item="WidgetB", stock=10, unit_price=500.00, category="Widget"),
    InventoryItem(item="GadgetX", stock=5, unit_price=750.00, category="Gadget"),
    InventoryItem(item="FakeItem", stock=0, unit_price=1000.00, category="Unknown"),
]


def init_db() -> None:
    """Create all tables and (re-)seed inventory rows.

    Uses merge() so repeated calls on an existing DB are idempotent for
    inventory rows.  ApprovedQuantity and InvoiceFingerprint rows are
    left untouched.
    """
    Base.metadata.create_all(_engine)
    with SessionLocal() as session:
        for item in _SEED_ITEMS:
            session.merge(item)
        session.commit()
    logger.debug("Database initialised at %s", DB_PATH)


def reset_approved_quantities() -> None:
    """Delete all approved-quantity records.

    Call before a test run to start with a clean cumulative slate.
    """
    with SessionLocal() as session:
        session.query(ApprovedQuantity).delete()
        session.commit()


def reset_fingerprints() -> None:
    """Delete all content fingerprint records.

    Call before a test run alongside reset_approved_quantities so fingerprints
    from one test do not bleed into the next and cause false duplicate flags.
    """
    with SessionLocal() as session:
        session.query(InvoiceFingerprint).delete()
        session.commit()


# ── Fingerprint helpers ───────────────────────────────────────────────────────


def compute_fingerprint(vendor: str, amount: float, due_date: str) -> str:
    """Return a SHA-256 hex digest of the canonical invoice content.

    Uses vendor + amount + due_date as the identity signal.  This detects
    the same economic transaction even when the invoice number differs between
    submissions (re-numbering is a common fraud vector).
    """
    raw = f"{(vendor or '').strip().lower()}|{amount:.2f}|{(due_date or '').strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def check_fingerprint(fingerprint: str) -> bool:
    """Return True if the fingerprint already exists (content-duplicate)."""
    with SessionLocal() as session:
        return (
            session.query(InvoiceFingerprint)
            .filter(InvoiceFingerprint.fingerprint == fingerprint)
            .first()
        ) is not None


def record_fingerprint(fingerprint: str, invoice_number: str, vendor: str) -> None:
    """Persist a paid invoice's content fingerprint to prevent re-payment."""
    with SessionLocal() as session:
        row = InvoiceFingerprint(
            fingerprint=fingerprint,
            invoice_number=invoice_number,
            vendor=vendor,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        session.merge(row)
        session.commit()
    logger.debug("Fingerprint recorded: %s (%s)", invoice_number, vendor)


# ── Query helpers ─────────────────────────────────────────────────────────────


def get_item(item_name: str) -> Optional[InventoryItem]:
    """Return the InventoryItem row for *item_name*, or None if not found."""
    with SessionLocal() as session:
        return session.get(InventoryItem, item_name)


def get_cumulative_approved_qty(item_name: str) -> int:
    """Return total units of *item_name* committed by previously-approved invoices."""
    with SessionLocal() as session:
        result = (
            session.query(func.sum(ApprovedQuantity.quantity))
            .filter(ApprovedQuantity.item == item_name)
            .scalar()
        )
        return result or 0


def record_approved_quantities(invoice_number: str, items: list[dict]) -> None:
    """Persist the approved quantities for *invoice_number*.

    *items* is a list of dicts with keys ``name`` and ``qty``.
    """
    with SessionLocal() as session:
        for entry in items:
            row = ApprovedQuantity(
                invoice_number=invoice_number,
                item=entry["name"],
                quantity=entry["qty"],
                approved_at=datetime.now(timezone.utc),
            )
            session.add(row)
        session.commit()
    logger.debug("Recorded approved quantities for %s: %s", invoice_number, items)


def get_total_approved_spend() -> float:
    """Return the sum of (qty × unit_price) for all approved items.

    Used by the critique agent to surface portfolio-level spend context.
    Returns 0.0 if the inventory has no unit_price data or nothing approved.
    """
    with SessionLocal() as session:
        result = (
            session.query(
                func.sum(ApprovedQuantity.quantity * InventoryItem.unit_price)
            )
            .join(InventoryItem, ApprovedQuantity.item == InventoryItem.item)
            .filter(InventoryItem.unit_price.isnot(None))
            .scalar()
        )
        return result or 0.0
