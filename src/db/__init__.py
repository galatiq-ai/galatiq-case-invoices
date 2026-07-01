"""Normalized relational database layer for the invoice pipeline.

Exposes the SQLAlchemy engine, session factory, all ORM models,
and the public write helpers used by pipeline agents.

Quick import surface:
    from src.db import init_normalized_db, record_invoice_run
"""

from src.db.models import (
    AuditEvent,
    CatalogItem,
    Invoice,
    InvoiceLineItem,
    InvoiceValidationFlag,
    Vendor,
)
from src.db.session import NSession, n_engine
from src.db.seed import seed_catalog, seed_vendors
from src.db.queries import (
    init_normalized_db,
    upsert_vendor,
    record_invoice_run,
)

__all__ = [
    "n_engine",
    "NSession",
    "Vendor",
    "CatalogItem",
    "Invoice",
    "InvoiceLineItem",
    "InvoiceValidationFlag",
    "AuditEvent",
    "init_normalized_db",
    "upsert_vendor",
    "record_invoice_run",
    "seed_catalog",
    "seed_vendors",
]
