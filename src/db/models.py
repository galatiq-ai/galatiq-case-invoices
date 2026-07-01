"""Normalized ORM models for the invoice pipeline.

Entity-Relationship overview
─────────────────────────────────────────────────────────────────────
  vendors (1) ──< invoices (1) ──< invoice_line_items
                                └─< invoice_validation_flags
                                └─< audit_events

  catalog_items (1) ──< invoice_line_items   (nullable FK — items not
                        in catalog have NULL here)

Tables
------
  vendors                  Master vendor/supplier directory.
  catalog_items            Product catalog with expected price + stock.
  invoices                 Invoice header (one row per pipeline run).
  invoice_line_items       Normalised line items linked to invoice header.
  invoice_validation_flags Per-invoice flags raised by the validation agent.
  audit_events             Immutable event log across all pipeline stages.

Design decisions
----------------
  - All PKs are auto-increment surrogate integers; natural keys (invoice_number,
    vendor name) are stored as UNIQUE columns so they remain queryable.
  - FKs use ON DELETE CASCADE so removing an invoice removes its children.
  - catalog_item_id on invoice_line_items is nullable: invoices may reference
    items not in the catalog (triggers "unknown_item" flag).
  - audit_events.invoice_id is nullable to allow pipeline-level errors recorded
    before an invoice row is created.
  - Timestamps stored as TEXT in ISO 8601 UTC so SQLite can sort them.
  - Uses SQLAlchemy 2.0 Mapped[] annotation form for type-safe relationships.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import (
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.db.session import n_engine


class Base(DeclarativeBase):
    pass


# ── 1. vendors ────────────────────────────────────────────────────────────────


class Vendor(Base):
    """Supplier/merchant master record.

    One row per unique vendor name encountered by the pipeline.
    New vendors are upserted on first invoice encounter.
    """

    __tablename__ = "vendors"
    __table_args__ = (UniqueConstraint("name", name="uq_vendor_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    invoices: Mapped[list[Invoice]] = relationship("Invoice", back_populates="vendor")

    def __repr__(self) -> str:
        return f"<Vendor id={self.id} name={self.name!r}>"


# ── 2. catalog_items ──────────────────────────────────────────────────────────


class CatalogItem(Base):
    """Product catalog: the authoritative price and stock source for validation.

    Mirrors the inventory table in ops_db but as a proper normalized
    entity with category, audit timestamp, and a surrogate PK.
    """

    __tablename__ = "catalog_items"
    __table_args__ = (UniqueConstraint("name", name="uq_catalog_item_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    standard_price: Mapped[Optional[float]] = mapped_column(nullable=True)
    stock_level: Mapped[int] = mapped_column(nullable=False, default=0)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    line_items: Mapped[list[InvoiceLineItem]] = relationship(
        "InvoiceLineItem", back_populates="catalog_item"
    )

    def __repr__(self) -> str:
        return (
            f"<CatalogItem id={self.id} name={self.name!r} price={self.standard_price}>"
        )


# ── 3. invoices ───────────────────────────────────────────────────────────────


class Invoice(Base):
    """Invoice header — one row per pipeline run.

    run_id is the pipeline's unique identifier (used to correlate with the
    JSON run log in the runs/ directory).  invoice_number is the number as
    printed on the document; it is NOT the PK because the same invoice number
    can appear in different formats (original + revised).
    """

    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_invoice_run_id"),
        Index("ix_invoices_invoice_number", "invoice_number"),
        Index("ix_invoices_vendor_id", "vendor_id"),
        Index("ix_invoices_status", "status"),
        Index("ix_invoices_fingerprint", "fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    vendor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[Optional[float]] = mapped_column(nullable=True)
    paid_amount: Mapped[Optional[float]] = mapped_column(nullable=True)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    due_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    invoice_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    vendor: Mapped[Optional[Vendor]] = relationship("Vendor", back_populates="invoices")
    line_items: Mapped[list[InvoiceLineItem]] = relationship(
        "InvoiceLineItem", back_populates="invoice", cascade="all, delete-orphan"
    )
    flags: Mapped[list[InvoiceValidationFlag]] = relationship(
        "InvoiceValidationFlag", back_populates="invoice", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        "AuditEvent", back_populates="invoice", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} run_id={self.run_id!r} status={self.status!r}>"


# ── 4. invoice_line_items ─────────────────────────────────────────────────────


class InvoiceLineItem(Base):
    """One row per line item on an invoice.

    catalog_item_id is nullable: items not found in catalog have NULL here and
    trigger an "unknown_item" validation flag.  unit_price is the price stated
    on the invoice (may differ from catalog standard_price).
    """

    __tablename__ = "invoice_line_items"
    __table_args__ = (
        Index("ix_line_items_invoice_id", "invoice_id"),
        Index("ix_line_items_catalog_item_id", "catalog_item_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    catalog_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("catalog_items.id", ondelete="SET NULL"), nullable=True
    )
    item_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Optional[int]] = mapped_column(nullable=True)
    unit_price: Mapped[Optional[float]] = mapped_column(nullable=True)
    line_total: Mapped[Optional[float]] = mapped_column(nullable=True)

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="line_items")
    catalog_item: Mapped[Optional[CatalogItem]] = relationship(
        "CatalogItem", back_populates="line_items"
    )

    def __repr__(self) -> str:
        return (
            f"<InvoiceLineItem id={self.id} item={self.item_name!r} "
            f"qty={self.quantity} price={self.unit_price}>"
        )


# ── 5. invoice_validation_flags ───────────────────────────────────────────────


class InvoiceValidationFlag(Base):
    """Persisted validation flag raised during Stage 2 of the pipeline.

    Storing flags in a relation (rather than as a JSON column) allows
    analytics queries like "how often is out_of_stock flagged?" and
    "which vendors trigger the most price_mismatch flags?".
    """

    __tablename__ = "invoice_validation_flags"
    __table_args__ = (
        Index("ix_flags_invoice_id", "invoice_id"),
        Index("ix_flags_issue_type", "issue_type"),
        Index("ix_flags_severity", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[int] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    issue_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    item_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    invoice: Mapped[Invoice] = relationship("Invoice", back_populates="flags")

    def __repr__(self) -> str:
        return (
            f"<Flag id={self.id} type={self.issue_type!r} severity={self.severity!r}>"
        )


# ── 6. audit_events ───────────────────────────────────────────────────────────


class AuditEvent(Base):
    """Immutable event log — one row per pipeline stage per invoice run.

    invoice_id is nullable so errors that occur before an Invoice row is
    committed (e.g. parse failure) can still be recorded by run_id alone.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_invoice_id", "invoice_id"),
        Index("ix_audit_events_run_id", "run_id"),
        Index("ix_audit_events_stage", "stage"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    invoice_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=True
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    invoice: Mapped[Optional[Invoice]] = relationship(
        "Invoice", back_populates="audit_events"
    )

    def __repr__(self) -> str:
        return (
            f"<AuditEvent id={self.id} stage={self.stage!r} "
            f"status={self.status!r} run_id={self.run_id!r}>"
        )


# ── DDL bootstrap (called by init_normalized_db) ──────────────────────────────


@event.listens_for(n_engine, "connect")
def _set_pragmas(dbapi_conn, _record) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_all() -> None:
    """Create all normalized tables (idempotent)."""
    Base.metadata.create_all(n_engine)
