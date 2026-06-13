"""Seed the inventory database. Run once before processing invoices.

Usage:
    python scripts/seed_inventory.py
"""
from src.database import init_db

def main():
    init_db()
    print("✓ Inventory database seeded at storage/inventory.db")


if __name__ == "__main__":
    main()
