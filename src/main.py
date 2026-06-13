#!/usr/bin/env python3
"""Galatiq Case: Invoice Processing Automation — Multi-Agent System.

Usage:
    # Seed the database first:
    python scripts/seed_inventory.py

    # Run on a single invoice:
    python main.py --invoice_path=data/invoices/invoice_1001.txt

    # Run on multiple invoices:
    python main.py --invoice_dir=data/invoices/

    # Run with verbose output:
    python main.py --invoice_path=data/invoices/invoice_1002.txt --verbose
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .database import init_db, get_connection
from .graph import run_pipeline, print_summary
from . import config


def main():
    parser = argparse.ArgumentParser(
        description="Multi-agent invoice processing automation system",
    )
    parser.add_argument(
        "--invoice_path",
        type=str,
        help="Path to a single invoice file to process",
    )
    parser.add_argument(
        "--invoice_dir",
        type=str,
        help="Directory containing invoice files to process in batch",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed output during each stage",
    )
    parser.add_argument(
        "--output_csv",
        type=str,
        default=None,
        help="Write batch results to a CSV file (e.g., results.csv)",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed the database and exit",
    )

    args = parser.parse_args()

    # Set global verbose flag for agents
    config.VERBOSE = args.verbose

    # Initialize database on every run (creates tables if they don't exist)
    init_db()

    if args.seed:
        print("✓ Database seeded successfully")
        return

    # Collect invoice files
    files_to_process: list[str] = []
    if args.invoice_path:
        path = Path(args.invoice_path)
        if path.exists():
            files_to_process.append(str(path))
        else:
            print(f"✗ File not found: {args.invoice_path}")
            sys.exit(1)

    if args.invoice_dir:
        dir_path = Path(args.invoice_dir)
        if dir_path.exists() and dir_path.is_dir():
            extensions = {".txt", ".json", ".csv", ".xml", ".pdf"}
            for f in sorted(dir_path.iterdir()):
                if f.suffix.lower() in extensions:
                    files_to_process.append(str(f))
        else:
            print(f"✗ Directory not found: {args.invoice_dir}")
            sys.exit(1)

    if not files_to_process:
        print("✗ No invoice files to process. Use --invoice_path or --invoice_dir.")
        sys.exit(1)

    # Process each invoice
    print(f"\nProcessing {len(files_to_process)} invoice(s)...\n")

    results = []
    for i, file_path in enumerate(files_to_process, 1):
        print(f"\n[{i}/{len(files_to_process)}] Processing: {file_path}")
        print("-" * 60)

        result = run_pipeline(file_path)
        results.append((file_path, result))

        print_summary(result)

    # Print batch summary
    if len(results) > 1:
        print("\n" + "=" * 60)
        print("  BATCH SUMMARY")
        print("=" * 60)
        for file_path, result in results:
            invoice = result.get("extracted_invoice", {})
            decision = result.get("approval_decision", {})
            payment = result.get("payment_result", {})

            inv_num = invoice.get("invoice_number", Path(file_path).stem) if invoice else Path(file_path).stem
            dec = decision.get("decision", "error") if decision else "error"
            pay = payment.get("status", "N/A") if payment else "N/A"
            err = result.get("error", "")

            status_icon = "OK" if not err else "ERR"
            print(f"  {status_icon} {inv_num:15s} | {dec.upper():10s} | Payment: {pay.upper():8s}"
                f"{' | ' + err if err else ''}")

    # Optional: write CSV with batch results
    if args.output_csv and results:
        import csv
        csv_path = Path(args.output_csv)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "file", "invoice_number", "vendor", "total", "currency",
                "decision", "payment_status", "flags", "error"
            ])
            writer.writeheader()
            for file_path, result in results:
                invoice = result.get("extracted_invoice") or {}
                decision = result.get("approval_decision") or {}
                payment = result.get("payment_result") or {}
                writer.writerow({
                    "file": Path(file_path).name,
                    "invoice_number": invoice.get("invoice_number", "N/A"),
                    "vendor": invoice.get("vendor", "N/A"),
                    "total": invoice.get("total", 0),
                    "currency": invoice.get("currency", "USD"),
                    "decision": decision.get("decision", "error"),
                    "payment_status": payment.get("status", "N/A"),
                    "flags": "; ".join(decision.get("required_actions", [])),
                    "error": result.get("error", ""),
                })
        print(f"\nResults written to {csv_path}")


if __name__ == "__main__":
    main()
