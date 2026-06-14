"""Create and seed the SQLite database.

    python seed_db.py            # idempotent: create + seed if missing
    python seed_db.py --reset    # drop the database first, then recreate

The app also seeds lazily on first connect; this script is the explicit path and
the only way to reset.
"""

import argparse

from backend import db


def _reset() -> None:
    for suffix in ("", "-wal", "-shm"):
        db.DB_PATH.with_name(db.DB_PATH.name + suffix).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="seed_db.py")
    parser.add_argument("--reset", action="store_true", help="drop the database before seeding")
    args = parser.parse_args()

    if args.reset:
        _reset()
        db._initialized = False
        print("reset: removed existing database")

    conn = db.connect()  # triggers schema + seed
    try:
        tables = ("vendors", "vendor_aliases", "purchase_orders", "po_lines")
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}
    finally:
        conn.close()

    print(f"database ready at {db.DB_PATH}")
    for table, n in counts.items():
        print(f"  {table:16} {n}")


if __name__ == "__main__":
    main()
