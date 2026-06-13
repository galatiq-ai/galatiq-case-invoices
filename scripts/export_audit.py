"""CLI to export audit events to CSV.

Usage:
  python scripts/export_audit.py --out out/audit.csv [--invoice INV-1001]

This uses `database.export_audit_csv` to write CSV output from the `logs/` DB.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from src.database import export_audit_csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export audit events to CSV")
    parser.add_argument("--out", "-o", required=True, help="Output CSV path")
    parser.add_argument("--invoice", "-i", help="Optional invoice number to filter")
    args = parser.parse_args(argv)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        export_audit_csv(out_path, invoice_number=args.invoice)
        print(f"Wrote audit CSV to {out_path}")
        return 0
    except Exception as e:
        print(f"Failed to export audit CSV: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
