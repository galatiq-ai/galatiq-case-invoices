"""Seed data for the normalized schema.

Run once via init_normalized_db() or directly: python -m src.db.seed
Idempotent — uses merge() so re-running is safe.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.db.models import CatalogItem, Vendor
from src.db.session import NSession

logger = logging.getLogger(__name__)

_NOW = datetime.now(timezone.utc).isoformat()

# ── Vendor seed data ──────────────────────────────────────────────────────────
# Sourced from all invoices in data/invoices/ at project creation time.
# New vendors encountered at runtime are upserted automatically.

_SEED_VENDORS: list[dict] = [
    {"name": "Widgets Inc.", "category": "Manufacturing"},
    {"name": "Consolidated Materials Group", "category": "Manufacturing"},
    {"name": "Summit Manufacturing Co.", "category": "Manufacturing"},
    {"name": "Atlas Industrial Supply", "category": "Industrial Supply"},
    {"name": "TechParts International", "category": "Technology"},
    {"name": "Precision Parts Ltd.", "category": "Manufacturing"},
    {"name": "Global Supply Chain Partners", "category": "Logistics"},
    {"name": "NoProd Industries", "category": "Manufacturing"},
    {"name": "Fraudster LLC", "category": "Unknown"},
]

# ── Catalog seed data ─────────────────────────────────────────────────────────
# Reference copy of the product catalog. Runtime stock tracking is handled
# exclusively via ops_db.inventory — this table is analytics-only.

_SEED_CATALOG: list[dict] = [
    {
        "name": "WidgetA",
        "standard_price": 250.00,
        "stock_level": 15,
        "category": "Widget",
    },
    {
        "name": "WidgetB",
        "standard_price": 500.00,
        "stock_level": 10,
        "category": "Widget",
    },
    {
        "name": "GadgetX",
        "standard_price": 750.00,
        "stock_level": 5,
        "category": "Gadget",
    },
    {
        "name": "FakeItem",
        "standard_price": 1000.00,
        "stock_level": 0,
        "category": "Unknown",
    },
]


def seed_vendors() -> None:
    with NSession() as session:
        for v in _SEED_VENDORS:
            existing = session.query(Vendor).filter_by(name=v["name"]).first()
            if not existing:
                session.add(
                    Vendor(name=v["name"], category=v["category"], created_at=_NOW)
                )
        session.commit()
    logger.debug("Vendor seed complete (%d records)", len(_SEED_VENDORS))


def seed_catalog() -> None:
    with NSession() as session:
        for c in _SEED_CATALOG:
            existing = session.query(CatalogItem).filter_by(name=c["name"]).first()
            if not existing:
                session.add(
                    CatalogItem(
                        name=c["name"],
                        standard_price=c["standard_price"],
                        stock_level=c["stock_level"],
                        category=c["category"],
                        created_at=_NOW,
                    )
                )
        session.commit()
    logger.debug("Catalog seed complete (%d records)", len(_SEED_CATALOG))


if __name__ == "__main__":
    from src.db.models import create_all

    create_all()
    seed_vendors()
    seed_catalog()
    print("Seed complete.")
