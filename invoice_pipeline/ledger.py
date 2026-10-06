"""The Ledger: one `arrivals` table, one row per arrival, in SQLite (WAL).

Connections run in autocommit; every write is an explicit `write_txn` (`BEGIN IMMEDIATE`).
"""

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

from invoice_pipeline.model import (
    ArrivalSummary,
    Decision,
    Finding,
    FindingCode,
    HistoryEntry,
    Ingested,
    Invoice,
    Outcome,
    PaymentIssue,
    QueueItem,
    finding,
    normalize_invoice_number,
    vendor_key,
)

DEFAULT_LEDGER_PATH = Path("ledger.db")
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE arrivals (
  id INTEGER PRIMARY KEY,
  seq INTEGER NOT NULL,
  arrived_at TEXT NOT NULL,
  source TEXT NOT NULL,
  vendor_key TEXT, invoice_number TEXT,
  revision TEXT, vendor_name TEXT,
  currency TEXT, total TEXT, amount_due TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('approved','needs_review','rejected','duplicate')),
  state TEXT NOT NULL CHECK (state IN
        ('needs_review','payment_pending','paid','logged_rejection','superseded','duplicate')),
  record TEXT NOT NULL,
  superseded_by INTEGER REFERENCES arrivals(id),
  revises INTEGER REFERENCES arrivals(id),
  duplicate_of INTEGER REFERENCES arrivals(id),
  amount_paid TEXT,
  payment_issue TEXT,
  resolution TEXT CHECK (resolution IN ('approve','reject')),
  resolution_reason TEXT, resolved_at TEXT,
  CHECK (state != 'payment_pending' OR
         (payment_issue IS NOT NULL AND amount_due IS NOT NULL AND currency IS NOT NULL)),
  CHECK (state != 'paid' OR (amount_paid IS NOT NULL AND currency IS NOT NULL)),
  CHECK ((decision = 'duplicate') = (state = 'duplicate')),
  CHECK (state != 'duplicate' OR duplicate_of IS NOT NULL)
);
CREATE INDEX arrivals_identity ON arrivals(vendor_key, invoice_number);
CREATE UNIQUE INDEX arrivals_seq ON arrivals(seq);
CREATE UNIQUE INDEX arrivals_one_pending ON arrivals(vendor_key, invoice_number)
  WHERE state = 'payment_pending';
"""

_NEXT_SEQ = "(SELECT COALESCE(MAX(seq), 0) + 1 FROM arrivals)"


class LedgerError(Exception):
    """The ledger database is not the expected schema."""


def connect(path: Path | str, read_only: bool = False) -> sqlite3.Connection:
    """Open (creating when new) a Ledger; a database with another schema version is refused.

    `read_only` opens an existing Ledger with `mode=ro`: nothing is created, a missing file fails.
    """
    if read_only:
        uri = f"{Path(path).resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=30)
    else:
        conn = sqlite3.connect(path, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and not read_only:
            version = _initialise(conn)
        if version == SCHEMA_VERSION and not read_only:
            _use_wal(conn)
    except BaseException:
        conn.close()
        raise
    if version != SCHEMA_VERSION:
        conn.close()
        raise LedgerError(f"{path}: unexpected user_version {version} (expected {SCHEMA_VERSION})")
    return conn


def _initialise(conn: sqlite3.Connection) -> int:
    """Create the schema and stamp its version in one write transaction; returns the version.

    An interruption rolls both back. The check is made under the write lock, so a concurrent
    first start that initialised the Ledger meanwhile is simply accepted, and a database that
    already has other tables keeps version 0 (refused by the caller).
    """
    with write_txn(conn):  # not executescript: it would COMMIT the open transaction first
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0 and not conn.execute("SELECT 1 FROM sqlite_master").fetchone():
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            version = SCHEMA_VERSION
    return version


def _use_wal(conn: sqlite3.Connection) -> None:
    """Switch the Ledger to WAL (persistent) unless it already is.

    The switch cannot run inside a transaction, and SQLite refuses it at once (SQLITE_BUSY,
    without waiting) while another first start holds a lock; a later connect then switches it.
    """
    if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
        return
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError as exc:
        if exc.sqlite_errorcode != sqlite3.SQLITE_BUSY:
            raise


@contextmanager
def write_txn(conn: sqlite3.Connection):
    """One short write transaction: commit on success, roll back on any exception."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def update_arrival(conn: sqlite3.Connection, arrival_id: int, **cols) -> None:
    """Update one arrival row; it takes the next global change sequence."""
    sets = "".join(f", {name} = ?" for name in cols)
    conn.execute(
        f"UPDATE arrivals SET seq = {_NEXT_SEQ}{sets} WHERE id = ?", (*cols.values(), arrival_id)
    )


def state_version(conn: sqlite3.Connection, identity: tuple[str, str]) -> int:
    """The identity's state version: the highest `seq` among its rows (0 when it has none)."""
    row = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) FROM arrivals WHERE vendor_key = ? AND invoice_number = ?",
        identity,
    ).fetchone()
    return row[0]


class ArrivalRow(NamedTuple):
    """The slice of an `arrivals` row that classification reads."""

    id: int
    state: str
    currency: str | None = None
    amount_paid: Decimal | None = None
    amount_due: Decimal | None = None


@dataclass(frozen=True)
class ArrivalContext:
    arrival: ArrivalSummary
    findings: list[Finding] = field(default_factory=list)  # Findings the Ledger itself raises


def _claimed_amount(row: ArrivalRow) -> Decimal | None:
    return row.amount_paid if row.state == "paid" else row.amount_due


def classify(rows: list[ArrivalRow], invoice: Invoice) -> ArrivalContext:
    """Route an arrival by its identity's history (pure). Claimed history = Paid or Pending."""
    claimed = [r for r in rows if r.state in ("paid", "payment_pending")]
    if invoice.identity() is None or not claimed:
        return ArrivalContext(ArrivalSummary(kind="new", amount_due=invoice.total))
    latest = max(claimed, key=lambda r: r.id)
    if not invoice.revision:
        return ArrivalContext(
            ArrivalSummary(
                kind="duplicate",
                duplicate_of=latest.id,
                paid_to_date=_claimed_amount(latest),
                claimed_state=latest.state,
            )
        )
    cur = invoice.currency
    paid = sum(
        (r.amount_paid or 0 for r in claimed if r.state == "paid" and r.currency == cur), Decimal(0)
    )
    pending = sum(
        (r.amount_due or 0 for r in claimed if r.state != "paid" and r.currency == cur), Decimal(0)
    )
    detail = (
        f"revision of an invoice already paid {paid:.2f} {cur} with {pending:.2f} {cur} pending; "
        "no payable amount until Payment Delta handling exists"
    )
    return ArrivalContext(
        ArrivalSummary(kind="revision", paid_to_date=paid + pending),
        [finding(FindingCode.REVISION_PAYMENT_DELTA, detail)],
    )


class CapExceeded(Exception):
    """A payment claim would push paid plus pending above the approved total."""


@dataclass(frozen=True)
class Arrival:
    """Everything the write phase records for one arrival."""

    source: str
    arrived_at: datetime
    ingested: Ingested
    findings: list[Finding]  # every Finding the Decision saw (all of them are recorded)
    decision: Decision
    amount_due: Decimal | None


def _dec(text: str | None) -> Decimal | None:
    return None if text is None else Decimal(text)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def read_identity(
    conn: sqlite3.Connection, identity: tuple[str, str]
) -> tuple[list[ArrivalRow], int]:
    """Read phase, no write lock: the identity's rows and its state version.

    The version is read first, so a write in between leaves it older than the rows and the
    write phase detects the change instead of trusting a stale snapshot.
    """
    version = state_version(conn, identity)
    rows = [
        ArrivalRow(
            r["id"], r["state"], r["currency"], _dec(r["amount_paid"]), _dec(r["amount_due"])
        )
        for r in conn.execute(
            "SELECT id, state, currency, amount_paid, amount_due FROM arrivals"
            " WHERE vendor_key = ? AND invoice_number = ? ORDER BY id",
            identity,
        )
    ]
    return rows, version


def claim(conn: sqlite3.Connection, arrival_id: int, now: datetime, **extra) -> None:
    """Inside a write transaction: cap guard, then Payment Pending with a provisional issue."""
    row = conn.execute("SELECT * FROM arrivals WHERE id = ?", (arrival_id,)).fetchone()
    amount, total = _dec(row["amount_due"]), _dec(row["total"])
    if amount is None or amount <= 0:
        raise ValueError(f"arrival {arrival_id} has no positive payable amount")
    settled = Decimal(0)
    for other in conn.execute(
        "SELECT state, amount_paid, amount_due, currency FROM arrivals WHERE vendor_key = ?"
        " AND invoice_number = ? AND state IN ('paid', 'payment_pending') AND id != ?",
        (row["vendor_key"], row["invoice_number"], arrival_id),
    ):
        if other["currency"] != row["currency"]:  # no reference rates: fail closed
            raise CapExceeded(
                f"payment cap: the invoice already has payments in another currency "
                f"({other['currency']}), so {row['currency']} cannot be checked against its total"
            )
        settled += _dec(other["amount_paid"] if other["state"] == "paid" else other["amount_due"])
    if total is None or settled + amount > total:
        raise CapExceeded(
            f"payment cap: {settled} already paid or pending plus {amount} exceeds the approved "
            f"total {total} {row['currency']}"
        )
    issue = PaymentIssue(what="attempt initiated; bank outcome unconfirmed", when=now)
    update_arrival(
        conn, arrival_id, state="payment_pending", payment_issue=issue.model_dump_json(), **extra
    )


_STATE = {
    Outcome.APPROVED: "needs_review",  # becomes payment_pending when claimed
    Outcome.NEEDS_REVIEW: "needs_review",
    Outcome.REJECTED: "logged_rejection",
    Outcome.DUPLICATE: "duplicate",
}


def _insert(conn: sqlite3.Connection, new: Arrival) -> int:
    invoice, decision = new.ingested.invoice, new.decision
    record = {
        "invoice": invoice,
        "findings": new.findings,
        "repairs": new.ingested.repairs,
        "unreadable_reason": new.ingested.unreadable_reason,
        "decision": decision,
    }
    cols = dict(
        arrived_at=new.arrived_at.isoformat(),
        source=new.source,
        decision=decision.outcome.value,
        state=_STATE[decision.outcome],
        amount_due=_text(new.amount_due),
        duplicate_of=decision.duplicate_of,
        record=json.dumps(record, default=lambda model: model.model_dump(mode="json")),
    )
    if invoice is not None:
        cols |= dict(
            vendor_key=vendor_key(invoice.vendor),
            invoice_number=normalize_invoice_number(invoice.invoice_number),
            revision=invoice.revision or None,
            vendor_name=invoice.vendor,
            currency=invoice.currency,
            total=_text(invoice.total),
        )
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    arrival_id = conn.execute(
        f"INSERT INTO arrivals (seq, {names}) VALUES ({_NEXT_SEQ}, {marks})", tuple(cols.values())
    ).lastrowid
    if decision.outcome is Outcome.APPROVED:
        claim(conn, arrival_id, new.arrived_at)
    return arrival_id


def record_if_unchanged(
    conn: sqlite3.Connection, identity: tuple[str, str], version: int, new: Arrival
) -> int | None:
    """Write phase: record the arrival (and its payment claim) only if the version is unchanged.

    Returns the new arrival id, or None with nothing written when the identity changed meanwhile.
    """
    with write_txn(conn):
        if state_version(conn, identity) != version:
            return None
        return _insert(conn, new)


VENDOR_HISTORY_LIMIT = 10  # most recent prior arrivals shown to the Critic


def vendor_history(conn: sqlite3.Connection, key: str) -> tuple[list[HistoryEntry], int]:
    """The vendor's newest prior arrivals (newest first) and the count of all of them."""
    total = conn.execute("SELECT COUNT(*) FROM arrivals WHERE vendor_key = ?", (key,)).fetchone()[0]
    entries = [
        HistoryEntry(
            number=r["invoice_number"] or "",
            total=_dec(r["total"]),
            currency=r["currency"],
            state=r["state"],
            date=r["invoice_date"],
        )
        for r in conn.execute(
            "SELECT invoice_number, total, currency, state,"
            " json_extract(record, '$.invoice.invoice_date') AS invoice_date"
            " FROM arrivals WHERE vendor_key = ? ORDER BY id DESC LIMIT ?",
            (key, VENDOR_HISTORY_LIMIT),
        )
    ]
    return entries, total


def review_queue(conn: sqlite3.Connection) -> list[QueueItem]:
    """Needs Review and Payment Pending arrivals in arrival order (read-only)."""
    return [
        QueueItem(
            arrival_id=r["id"],
            vendor=r["vendor_name"],
            invoice_number=r["invoice_number"],
            source=r["source"],
            total=_dec(r["total"]),
            currency=r["currency"],
            state=r["state"],
            reasons=json.loads(r["record"])["decision"]["reasons"],
            payment_issue=r["payment_issue"]
            and PaymentIssue.model_validate_json(r["payment_issue"]),
        )
        for r in conn.execute(
            "SELECT * FROM arrivals WHERE state IN ('needs_review', 'payment_pending')"
            " ORDER BY arrived_at, id"
        )
    ]


def record(conn: sqlite3.Connection, new: Arrival) -> int:
    """Record an arrival that matches no identity (Unreadable, Partial, Incomplete Identity)."""
    with write_txn(conn):
        return _insert(conn, new)
