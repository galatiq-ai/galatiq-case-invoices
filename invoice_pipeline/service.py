"""The service API used by presenters: process files, list the queue, resolve entries.

Orchestration only: every rule lives in ingestion, validation, approval, ledger or payment.
"""

import json
import logging
import sqlite3
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from invoice_pipeline import catalog, ingestion, ledger, payment
from invoice_pipeline.approval import decide, decide_unreadable
from invoice_pipeline.catalog import Catalog, CatalogError
from invoice_pipeline.critic import build_case_file, offline_agents
from invoice_pipeline.model import Agents, Event, FindingCode, Ingested, QueueItem, vendor_key
from invoice_pipeline.validation import validate

MAX_DECIDE_ATTEMPTS = 3  # read-decide-write rounds before a processing failure

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Runtime:
    catalog: Catalog
    ledger_path: Path
    tier: str = "offline"
    agents: Agents = field(default_factory=offline_agents)
    pay_fn: payment.PayFn = payment.mock_payment
    on_event: Callable[[Event], None] = lambda event: None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


class ProcessingFailure(Exception):
    """An invoice of a file failed after ingestion, at the named stage."""

    def __init__(self, file: str, stage: str, error: str):
        super().__init__(f"{file}: {stage}: {error}")
        self.file, self.stage, self.error = file, stage, error


def _notify(rt: Runtime, event: Event) -> None:
    """Deliver an event; an observer error is logged and never changes what was processed."""
    try:
        rt.on_event(event)
    except Exception:
        log.warning("event observer failed on %s for %s", event.name, event.file, exc_info=True)


@contextmanager
def _stage(file: str, stage: str):
    """Report any exception inside as a ProcessingFailure naming the file and stage."""
    try:
        yield
    except ProcessingFailure:
        raise
    except Exception as exc:
        raise ProcessingFailure(file, stage, f"{type(exc).__name__}: {exc}") from exc


def record_arrival(conn, ingested: Ingested, source: str, rt: Runtime) -> int:
    """Validate, then read-decide-write until the identity's version holds; returns the arrival id.

    No database lock is held while deciding. A changed identity discards the Decision and decides
    again on the fresh history; `MAX_DECIDE_ATTEMPTS` changes in a row is a processing failure.
    """
    invoice = ingested.invoice
    with _stage(source, "validation"):
        findings = [*ingested.findings, *validate(invoice, rt.catalog)]
    _notify(rt, Event("validated", source, {"findings": [f.code.value for f in findings]}))
    identity, key = invoice.identity(), vendor_key(invoice.vendor)
    for attempt in range(1, MAX_DECIDE_ATTEMPTS + 1):
        with _stage(source, "ledger"):
            rows, version = ledger.read_identity(conn, identity) if identity else ([], 0)
            ctx = ledger.classify(rows, invoice)
            history, total = ledger.vendor_history(conn, key) if key else ([], 0)
        all_findings = [*findings, *ctx.findings]
        with _stage(source, "approval"):
            case_file = build_case_file(
                invoice, all_findings, ctx.arrival, None, rt.catalog, history, total
            )
            decision = decide(case_file, rt.agents)
        new = ledger.Arrival(
            source, rt.now(), ingested, all_findings, decision, ctx.arrival.amount_due
        )
        with _stage(source, "ledger"):
            if identity is None:  # Partial/Incomplete Identity: nothing to version-check
                return ledger.record(conn, new)
            arrival_id = ledger.record_if_unchanged(conn, identity, version, new)
        if arrival_id is not None:
            return arrival_id
        if attempt < MAX_DECIDE_ATTEMPTS:
            _notify(rt, Event("redecided", source, {"attempt": attempt}))
    raise ProcessingFailure(
        source,
        "ledger",
        f"identity changed {MAX_DECIDE_ATTEMPTS} times while deciding; not recorded",
    )


@dataclass(frozen=True)
class ArrivalResult:
    """One recorded arrival as presenters see it."""

    arrival_id: int
    source: str
    invoice_number: str | None
    vendor: str | None
    decision: str
    precedence_row: int
    state: str
    finding_codes: list[str]
    reasons: list[str]
    model_notes: str  # "offline tier", a role name, or "none" (no model role ran)


def arrival_result(conn, arrival_id: int) -> ArrivalResult:
    row = conn.execute("SELECT * FROM arrivals WHERE id = ?", (arrival_id,)).fetchone()
    record = json.loads(row["record"])
    decision = record["decision"]
    notes = "none"
    for role in ("escalate_review", "advisory"):
        if call := decision.get(role):
            notes = "offline tier" if call["tier"] == "offline" else role
            break
    return ArrivalResult(
        arrival_id=arrival_id,
        source=row["source"],
        invoice_number=row["invoice_number"],
        vendor=row["vendor_name"],
        decision=row["decision"],
        precedence_row=decision["precedence_row"],
        state=row["state"],
        finding_codes=[f["code"] for f in record["findings"]],
        reasons=decision["reasons"],
        model_notes=notes,
    )


def pay_claimed(conn, arrival_id: int, rt: Runtime, source: str) -> None:
    """Call the bank for a committed claim and emit the outcome; the claim stays on any failure."""
    with _stage(source, "payment"):
        issue = payment.pay(conn, arrival_id, rt.pay_fn, rt.now)
    if issue is None:
        _notify(rt, Event("payment_sent", source, {"arrival_id": arrival_id}))
    else:
        _notify(rt, Event("payment_failed", source, {"issue": issue.model_dump(mode="json")}))


class ResolutionRefused(Exception):
    """A Resolution was refused; nothing changed."""


_REJECT_ONLY = {
    FindingCode.UNREADABLE_DOCUMENT: "it is an unreadable document",
    FindingCode.PARTIAL_IDENTITY: "its identity is incomplete, so the duplicate check cannot run",
    FindingCode.MISSING_REQUIRED_FIELD: "a required amount is missing",
    FindingCode.NONPOSITIVE_TOTAL: "its total is not positive",
}


def _refusal(row, action: str) -> str | None:
    """Why this Resolution must be refused, or None when it may proceed."""
    refused_by_state = {
        "payment_pending": "payment pending: v1 has no settlement action, check the bank outside",
        "superseded": f"replaced by its later arrival #{row['superseded_by']}; it needs no action",
        "duplicate": f"a Duplicate of arrival #{row['duplicate_of']}, which is already claimed",
        "logged_rejection": (
            "rejected: only a new arrival of a corrected invoice can lead to payment"
        ),
        "paid": "already paid",
    }
    if row["state"] in refused_by_state:
        return f"arrival #{row['id']} is {refused_by_state[row['state']]}"
    if row["resolution"]:
        return f"arrival #{row['id']} already has a Resolution ({row['resolution']})"
    if action == "reject":
        return None
    codes = {FindingCode(f["code"]) for f in json.loads(row["record"])["findings"]}
    for code, why in _REJECT_ONLY.items():
        if code in codes:
            return f"cannot approve arrival #{row['id']}: {why}; reject it instead"
    if row["vendor_key"] is None or row["invoice_number"] is None:
        return f"cannot approve arrival #{row['id']}: its identity is incomplete; reject it instead"
    if row["amount_due"] is None or Decimal(row["amount_due"]) <= 0:
        return f"cannot approve arrival #{row['id']}: no known positive payable amount; reject it"
    return None


def resolve(rt: Runtime, arrival_id: int, action: str, reason: str) -> ArrivalResult:
    """Approve or reject one Needs Review arrival, with a mandatory reason (no model, no editing).

    One short write transaction re-reads the row, so a stale view is refused; a refusal (including
    a payment-cap failure) rolls back and leaves the entry intact. Approval pays after the commit.
    """
    reason = (reason or "").strip()
    if action not in ("approve", "reject"):
        raise ResolutionRefused("a Resolution is either approve or reject")
    if not reason:
        raise ResolutionRefused("a Resolution needs a reason")
    conn = ledger.connect(rt.ledger_path)
    try:
        with ledger.write_txn(conn):
            row = conn.execute("SELECT * FROM arrivals WHERE id = ?", (arrival_id,)).fetchone()
            if row is None:
                raise ResolutionRefused(f"no arrival #{arrival_id}")
            if why := _refusal(row, action):
                raise ResolutionRefused(why)
            now = rt.now()
            ruling = dict(resolution=action, resolution_reason=reason, resolved_at=now.isoformat())
            if action == "reject":
                ledger.update_arrival(conn, arrival_id, state="logged_rejection", **ruling)
            else:
                pending = conn.execute(
                    "SELECT id FROM arrivals WHERE vendor_key = ? AND invoice_number = ?"
                    " AND state = 'payment_pending'",
                    (row["vendor_key"], row["invoice_number"]),
                ).fetchone()
                if pending:
                    raise ResolutionRefused(
                        f"cannot approve arrival #{arrival_id}: payment pending on arrival "
                        f"#{pending['id']}; v1 has no settlement action, check the bank outside"
                    )
                try:
                    ledger.claim(conn, arrival_id, now, **ruling)
                except ledger.CapExceeded as exc:
                    raise ResolutionRefused(str(exc)) from exc
        if action == "approve":
            pay_claimed(conn, arrival_id, rt, row["source"])
        return arrival_result(conn, arrival_id)
    finally:
        conn.close()


class BootstrapError(Exception):
    """The run cannot start: unsupported tier, or a database with the wrong schema."""


def ledger_path_of(args) -> Path:
    return Path(args.ledger or ledger.DEFAULT_LEDGER_PATH)


def bootstrap(args) -> Runtime:
    """Seed a missing inventory, check both schemas, and select the tier (offline only so far)."""
    tier = args.llm or "offline"
    if tier != "offline":
        raise BootstrapError(f"LLM tier {tier!r} is not available yet; use --llm offline")
    inventory = Path(args.inventory or catalog.DEFAULT_INVENTORY_PATH)
    ledger_path = ledger_path_of(args)
    try:
        if not inventory.exists():
            catalog.seed(inventory)
        loaded = catalog.load_catalog(inventory)
        ledger.connect(ledger_path).close()
    except (CatalogError, ledger.LedgerError, sqlite3.Error, OSError) as exc:
        raise BootstrapError(str(exc)) from exc
    return Runtime(catalog=loaded, ledger_path=ledger_path, tier=tier)


def collect_files(path: Path | str) -> list[Path]:
    """One file, or every file directly in a directory in ascending filename order."""
    path = Path(path)
    return sorted(p for p in path.iterdir() if p.is_file()) if path.is_dir() else [path]


def process_path(path: Path | str, rt: Runtime) -> BatchResult:
    """Ingest one file, then decide, record and (when Approved) pay each invoice in it
    independently; a failing invoice is reported and the rest of the file continues."""
    path = Path(path)
    results, failed = [], []
    conn = ledger.connect(rt.ledger_path)
    try:
        for ingested in ingestion.ingest(path):
            try:
                results.append(_process_one(conn, ingested, path.name, rt))
            except Exception as exc:
                failed.append(_failure(rt, path, exc))
    finally:
        conn.close()
    return BatchResult(results, failed)


def _process_one(conn, ingested: Ingested, source: str, rt: Runtime) -> ArrivalResult:
    _notify(rt, Event("ingested", source, {"unreadable": ingested.invoice is None}))
    if ingested.invoice is None:
        with _stage(source, "approval"):
            decision = decide_unreadable(ingested.findings)
        new = ledger.Arrival(source, rt.now(), ingested, ingested.findings, decision, None)
        with _stage(source, "ledger"):
            arrival_id = ledger.record(conn, new)
    else:
        arrival_id = record_arrival(conn, ingested, source, rt)
    result = arrival_result(conn, arrival_id)
    _notify(
        rt, Event("decided", source, {"outcome": result.decision, "row": result.precedence_row})
    )
    if result.state == "payment_pending":  # Approved and claimed
        pay_claimed(conn, arrival_id, rt, source)
        result = arrival_result(conn, arrival_id)
    return result


@dataclass(frozen=True)
class BatchResult:
    results: list[ArrivalResult]
    failed: list[ProcessingFailure]

    def counts(self) -> dict[str, int]:
        return dict(Counter(r.state for r in self.results))


def _failure(rt: Runtime, path: Path, exc: Exception) -> ProcessingFailure:
    """Report an exception as a ProcessingFailure (emitting `file_failed`) and return it."""
    failure = (
        exc
        if isinstance(exc, ProcessingFailure)
        else ProcessingFailure(Path(path).name, "processing", f"{type(exc).__name__}: {exc}")
    )
    _notify(
        rt, Event("file_failed", failure.file, {"stage": failure.stage, "error": failure.error})
    )
    return failure


def run_batch(paths: list[Path], rt: Runtime) -> BatchResult:
    """Process files in the given order; a processing failure is reported and the rest continue.

    Every committed invoice is in `results`, also when another invoice of its file failed.
    """
    results, failed = [], []
    for path in paths:
        try:
            batch = process_path(path, rt)
        except Exception as exc:  # the file itself could not be processed (e.g. the Ledger)
            failed.append(_failure(rt, path, exc))
            continue
        results.extend(batch.results)
        failed.extend(batch.failed)
    return BatchResult(results, failed)


def review_queue(ledger_path: Path) -> list[QueueItem]:
    """The Review Queue: the same Ledger query every presenter uses.

    Read-only: the Ledger must already exist and is never created, migrated or seeded.
    """
    if not ledger_path.exists():
        raise BootstrapError(f"ledger not found: {ledger_path}")
    try:
        conn = ledger.connect(ledger_path, read_only=True)
    except (ledger.LedgerError, sqlite3.Error) as exc:
        raise BootstrapError(str(exc)) from exc
    try:
        return ledger.review_queue(conn)
    finally:
        conn.close()
