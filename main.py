"""CLI entrypoint for the Galatiq invoice processing agent system.

Usage:
  # Single invoice
  python main.py --invoice_path=data/invoices/invoice_1001.txt

  # Batch mode — all invoices in data/invoices/
  python main.py --batch

  # Batch mode with explicit directory
  python main.py --batch --invoice_dir=data/invoices

Options:
  --invoice_path PATH   Path to a single invoice file to process.
  --batch               Process every invoice in the invoice directory.
  --invoice_dir DIR     Directory to scan in batch mode (default: data/invoices).
  --log_level LEVEL     Logging verbosity: DEBUG, INFO, WARNING (default: INFO).
  --reset_db            Reset the approved_quantities table before running
                        (useful for a clean cumulative-stock baseline).
"""

import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env before importing anything that reads env vars
load_dotenv()

from config import INVOICE_DIR, LOG_DIR  # noqa: E402
from src.db.queries import init_normalized_db  # noqa: E402
from src.graph.graph import run_pipeline  # noqa: E402
from src.graph.state import InvoiceState  # noqa: E402
from src.ops_db import init_db, reset_approved_quantities  # noqa: E402


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_separator(char: str = "─", width: int = 70) -> None:
    print(char * width)


def _print_result(state: InvoiceState) -> None:
    """Print a structured human-readable summary of the pipeline result."""
    extracted = state.get("extracted_data", {})
    flags = state.get("validation_flags", [])
    decision = state.get("final_decision", "unknown")
    payment = state.get("payment_result", {})
    warnings = extracted.get("extraction_warnings", [])
    llm_calls = state.get("llm_calls", 0)
    total_tokens = state.get("total_tokens", 0)

    _print_separator()
    print(f"  Invoice:  {extracted.get('invoice_number') or 'N/A'}")
    print(f"  Vendor:   {extracted.get('vendor') or 'N/A'}")
    print(
        f"  Amount:   {extracted.get('amount') or 0:,.2f} {extracted.get('currency') or 'USD'}"
    )
    print(f"  Due:      {extracted.get('due_date') or 'N/A'}")
    _print_separator()

    if warnings:
        print("  EXTRACTION WARNINGS:")
        for w in warnings:
            print(f"    ⚠  {w}")

    if flags:
        print("  VALIDATION FLAGS:")
        for f in flags:
            print(f"    [{f['issue_type'].upper()}] {f['item']}: {f['detail']}")
    else:
        print("  Validation: CLEAN (no flags)")

    _print_separator()
    if decision == "approved":
        print("  DECISION:  ✓ APPROVED")
    elif decision == "rejected":
        print("  DECISION:  ✗ REJECTED")
    elif decision == "needs_review":
        print("  DECISION:  ⚑ NEEDS REVIEW")
    else:
        print(f"  DECISION:  ? {decision}")

    if state.get("approval_reasoning"):
        print(
            f"\n  Approval reasoning:\n{state['approval_reasoning'][:500]}{'...' if len(state.get('approval_reasoning', '')) > 500 else ''}"
        )

    if state.get("critique_notes"):
        print(
            f"\n  Critique notes:\n{state['critique_notes'][:400]}{'...' if len(state.get('critique_notes', '')) > 400 else ''}"
        )

    if payment:
        print(f"\n  Payment result: {payment.get('status', 'N/A')}")

    print(f"\n  LLM calls: {llm_calls} | Tokens: {total_tokens}")
    _print_separator()


def _persist_run_log(state: InvoiceState) -> str:
    """Write the full pipeline state to a JSON file in the runs/ directory."""
    os.makedirs(LOG_DIR, exist_ok=True)
    run_id = state.get("run_id", "unknown")
    log_path = os.path.join(LOG_DIR, f"{run_id}.json")
    # State contains non-serialisable objects — convert to primitive-safe dict
    safe_state = {
        k: v
        for k, v in state.items()
        if isinstance(v, (str, int, float, bool, list, dict, type(None)))
    }
    with open(log_path, "w") as fh:
        json.dump(safe_state, fh, indent=2, default=str)
    return log_path


def process_single(invoice_path: str) -> InvoiceState:
    """Run the pipeline for one invoice and print the result."""
    run_id = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    )
    print(f"\nProcessing: {invoice_path}  (run_id={run_id})")

    t0 = time.monotonic()
    state: InvoiceState = run_pipeline({"invoice_path": invoice_path, "run_id": run_id})
    elapsed = time.monotonic() - t0

    _print_result(state)
    log_path = _persist_run_log(state)
    print(f"  Run log:  {log_path}  ({elapsed:.1f}s)\n")
    return state


_FORMAT_PRIORITY = {".pdf": 0, ".txt": 1, ".json": 2, ".csv": 3, ".xml": 4}


def process_batch(invoice_dir: str) -> list[InvoiceState]:
    """Process every invoice file in *invoice_dir* and print a summary report.

    When multiple formats exist for the same filename stem (e.g. invoice_1011.pdf
    and invoice_1011.txt), only the highest-priority format is processed:
    PDF > TXT > JSON > CSV > XML.
    """
    extensions = set(_FORMAT_PRIORITY)
    all_files = [
        p
        for p in Path(invoice_dir).iterdir()
        if p.is_file() and p.suffix.lower() in extensions
    ]

    # Keep only the best format per stem
    by_stem: dict[str, Path] = {}
    for p in all_files:
        stem = p.stem.lower()
        current = by_stem.get(stem)
        if (
            current is None
            or _FORMAT_PRIORITY[p.suffix.lower()]
            < _FORMAT_PRIORITY[current.suffix.lower()]
        ):
            by_stem[stem] = p

    paths = sorted(by_stem.values())
    if not paths:
        print(f"No invoice files found in {invoice_dir}")
        return []

    skipped = len(all_files) - len(paths)
    skip_note = f" ({skipped} lower-priority duplicate(s) skipped)" if skipped else ""
    print(f"\nBatch mode: {len(paths)} invoice(s) in {invoice_dir}{skip_note}\n")
    results: list[InvoiceState] = []

    for path in paths:
        try:
            state = process_single(str(path))
            results.append(state)
        except Exception as exc:
            logging.getLogger(__name__).error("Pipeline failed for %s: %s", path, exc)
            results.append({"invoice_path": str(path), "error": str(exc)})

    # ── Batch summary report ──────────────────────────────────────────────────
    _print_separator("═")
    print("  BATCH SUMMARY REPORT")
    _print_separator("═")

    approved = [r for r in results if r.get("final_decision") == "approved"]
    rejected = [r for r in results if r.get("final_decision") == "rejected"]
    needs_review = [r for r in results if r.get("final_decision") == "needs_review"]
    errors = [r for r in results if r.get("error")]

    print(f"  Total processed : {len(results)}")
    print(f"  Approved        : {len(approved)}")
    print(f"  Rejected        : {len(rejected)}")
    print(f"  Needs review    : {len(needs_review)}")
    print(f"  Errors          : {len(errors)}")

    total_approved_amount = sum(
        (r.get("extracted_data", {}).get("amount") or 0) for r in approved
    )
    total_rejected_amount = sum(
        (r.get("extracted_data", {}).get("amount") or 0) for r in rejected
    )
    print(f"\n  Total approved value  : ${total_approved_amount:>12,.2f}")
    print(f"  Total rejected value  : ${total_rejected_amount:>12,.2f}")

    total_llm_calls = sum(r.get("llm_calls", 0) for r in results)
    total_tokens = sum(r.get("total_tokens", 0) for r in results)
    print(f"\n  Total LLM calls       : {total_llm_calls}")
    print(f"  Total tokens consumed : {total_tokens}")

    if rejected or needs_review:
        print("\n  Flagged invoice IDs:")
        for r in rejected + needs_review:
            ext = r.get("extracted_data", {})
            inv_num = ext.get("invoice_number") or r.get("invoice_path", "?")
            dec = r.get("final_decision", "unknown")
            print(f"    [{dec.upper()}] {inv_num}")

    _print_separator("═")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Galatiq Invoice Processing Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--invoice_path", help="Path to a single invoice file")
    parser.add_argument(
        "--batch", action="store_true", help="Process all invoices in directory"
    )
    parser.add_argument(
        "--invoice_dir", default=INVOICE_DIR, help="Invoice directory for batch mode"
    )
    parser.add_argument(
        "--log_level", default="INFO", help="Logging level (DEBUG/INFO/WARNING)"
    )
    parser.add_argument(
        "--reset_db", action="store_true", help="Reset approved_quantities before run"
    )
    args = parser.parse_args()

    _setup_logging(args.log_level)
    logger = logging.getLogger(__name__)

    # Initialise both databases (idempotent)
    init_db()
    init_normalized_db()

    if args.reset_db:
        reset_approved_quantities()
        logger.info("Approved quantities table reset")

    if args.batch:
        process_batch(args.invoice_dir)
    elif args.invoice_path:
        process_single(args.invoice_path)
    elif args.reset_db:
        pass  # reset already ran above; exit cleanly
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
