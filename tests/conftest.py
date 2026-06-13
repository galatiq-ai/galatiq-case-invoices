# tests/conftest.py
"""Shared pytest fixtures."""

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

INVOICES_DIR = Path(__file__).resolve().parent.parent / "data" / "invoices"


@pytest.fixture
def invoice_path():
    return INVOICES_DIR


@pytest.fixture(autouse=True)
def init_test_db(tmp_path, monkeypatch):
    """Use a fresh temporary DB for every test — prevents test pollution."""
    import src.database as database
    test_db = tmp_path / "test_inventory.db"
    monkeypatch.setattr(database, "DB_PATH", test_db)
    database.init_db()
    yield
