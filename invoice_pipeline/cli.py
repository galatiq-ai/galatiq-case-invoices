"""Command line: `--invoice_path` runs a batch, `review --list` prints the Review Queue.

Presentation only. There is deliberately no command that resolves or settles an entry.
"""

import argparse
import dataclasses
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from invoice_pipeline import service
from invoice_pipeline.model import Event, QueueItem

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2  # usage = argparse's own code


def _global_flags(parser: argparse.ArgumentParser, default=None) -> None:
    parser.add_argument(
        "--llm", choices=["grok", "ollama", "offline"], default=default, help="LLM tier"
    )
    parser.add_argument("--ledger", default=default, help="Ledger database (default: ledger.db)")
    parser.add_argument(
        "--inventory", default=default, help="inventory database (default: inventory.db)"
    )
    parser.add_argument(
        "--json", action="store_true", default=default, help="JSON lines even on a terminal"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="main.py", description="Invoice processing pipeline.")
    parser.add_argument("--invoice_path", help="an invoice file, or a directory of invoices")
    _global_flags(parser)
    commands = parser.add_subparsers(dest="command")
    review = commands.add_parser("review", help="show the Review Queue (read-only)")
    review.add_argument("--list", action="store_true", required=True, help="list the queue")
    _global_flags(review, default=argparse.SUPPRESS)  # also accepted after the subcommand
    return parser


def _identity(item: QueueItem) -> str:
    return (
        f"{item.vendor or '<vendor missing>'} / {item.invoice_number or '<invoice number missing>'}"
    )


class JsonLines:
    """One JSON object per line: startup, pipeline events, arrivals, summary."""

    def __init__(self, out):
        self.out = out

    def emit(self, obj: dict) -> None:
        print(json.dumps(obj, default=str), file=self.out)

    def startup(self, tier: str) -> None:
        self.emit({"event": "startup", "tier": tier})

    def event(self, event: Event) -> None:
        self.emit({"event": event.name, "file": event.file, **event.detail})

    def finish(self, batch: service.BatchResult) -> None:
        for result in batch.results:
            self.emit({"event": "arrival", **dataclasses.asdict(result)})
        self.emit({"event": "summary", "counts": batch.counts(), "failed": len(batch.failed)})

    def queue(self, items: list[QueueItem]) -> None:
        for item in items:
            self.emit({**item.model_dump(mode="json"), "identity": _identity(item)})


class RichOutput:
    """Terminal output: a header, progress lines, and a summary table."""

    def __init__(self, out):
        self.console = Console(file=out, markup=False, highlight=False)

    def startup(self, tier: str) -> None:
        self.console.print(f"Invoice pipeline - LLM tier: {tier}", style="bold")

    def event(self, event: Event) -> None:
        self.console.print(f"  {event.name}: {event.file}", style="dim")

    def finish(self, batch: service.BatchResult) -> None:
        table = Table("File", "Invoice", "Vendor", "Decision", "State", "Findings")
        for r in batch.results:
            table.add_row(
                r.source,
                r.invoice_number or "-",
                r.vendor or "-",
                r.decision,
                r.state,
                ", ".join(r.finding_codes),
            )
        self.console.print(table)
        counts = ", ".join(f"{state}: {n}" for state, n in batch.counts().items()) or "none"
        self.console.print(f"Outcomes - {counts}; failed: {len(batch.failed)}", style="bold")

    def queue(self, items: list[QueueItem]) -> None:
        table = Table("ID", "Identity", "Source", "Total", "State", "Reasons")
        for item in items:
            total = f"{item.total} {item.currency}" if item.total is not None else "-"
            table.add_row(
                str(item.arrival_id),
                _identity(item),
                item.source,
                total,
                item.state,
                "; ".join(item.reasons),
            )
        self.console.print(table)


def _error(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)


def _run_batch(args, ui) -> int:
    path = Path(args.invoice_path)
    if not path.exists():
        _error(f"invoice path not found: {path}")
        return EXIT_FAILED
    try:
        rt = service.bootstrap(args)
    except service.BootstrapError as exc:
        _error(f"cannot start: {exc}")
        return EXIT_FAILED
    rt = dataclasses.replace(rt, on_event=ui.event)
    ui.startup(rt.tier)
    batch = service.run_batch(service.collect_files(path), rt)
    ui.finish(batch)
    for failure in batch.failed:
        _error(str(failure))
    if batch.failed:
        _error(f"{len(batch.failed)} processing failure(s); see the errors above")
        return EXIT_FAILED
    return EXIT_OK


def _list_queue(args, ui) -> int:
    try:
        items = service.review_queue(service.ledger_path_of(args))
    except service.BootstrapError as exc:
        _error(f"cannot start: {exc}")
        return EXIT_FAILED
    ui.queue(items)
    return EXIT_OK


def main(argv: list[str] | None = None, out=None) -> int:
    out = out or sys.stdout
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits itself: 2 for a usage error, 0 for --help
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if args.command is None and not args.invoice_path:
        parser.print_usage(sys.stderr)
        _error("give --invoice_path, or the review command")
        return EXIT_USAGE
    ui = JsonLines(out) if args.json or not out.isatty() else RichOutput(out)
    return _list_queue(args, ui) if args.command == "review" else _run_batch(args, ui)
