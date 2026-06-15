"""Invoice repository — the single door for writing invoice rows.

Status changes go through set_status() so assert_transition() is unavoidable and
every transition is recorded on the trace. Callers (the HTTP handler, the
pipeline, the CLI) never UPDATE invoices.status directly — that's the contract
that gives the state machine teeth.
"""

import json
import uuid
from enum import Enum

from . import tracing
from .impossible import impossible
from .schemas import ExtractedInvoice
from .statuses import Status, assert_transition
from .unit_of_work import UnitOfWork


def new_trace_id() -> str:
    return f"trc_{uuid.uuid4().hex[:12]}"


def create_invoice(
    uow: UnitOfWork, source_path: str, source_format: str, *, trace_id: str | None = None
) -> int:
    trace_id = trace_id or new_trace_id()
    invoice_id = uow.execute(
        "INSERT INTO invoices (trace_id, status, source_path, source_format) VALUES (?, ?, ?, ?)",
        (trace_id, Status.RECEIVED.value, source_path, source_format),
    ).lastrowid
    tracing.emit(
        uow, invoice_id, "lifecycle", "created",
        {"summary": f"received {source_format} from {source_path}", "status": Status.RECEIVED.value},
    )
    return invoice_id


def get_status(uow: UnitOfWork, invoice_id: int) -> Status:
    rows = uow.query("SELECT status FROM invoices WHERE id = ?", (invoice_id,))
    if not rows:
        impossible("status lookup on a missing invoice", {"invoice_id": invoice_id})
    return Status(rows[0]["status"])


def set_status(uow: UnitOfWork, invoice_id: int, new: Status) -> None:
    current = get_status(uow, invoice_id)
    assert_transition(current, new)
    uow.execute(
        "UPDATE invoices SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (new.value, invoice_id),
    )
    tracing.emit(
        uow, invoice_id, "lifecycle", "transition",
        {"from": current.value, "to": new.value, "summary": f"{current.value} -> {new.value}"},
    )


def save_extraction(uow: UnitOfWork, invoice_id: int, ex: ExtractedInvoice) -> None:
    """Persist the extracted fields onto the invoice row and its line items. The
    full extraction (other_charges, issues_noticed, legibility) stays in the
    trace; the columns hold what later stages match and decide against."""
    charges = sum(c.amount for c in ex.other_charges) if ex.other_charges else None
    uow.execute(
        "UPDATE invoices SET invoice_number=?, vendor_raw=?, currency=?, invoice_date=?,"
        " due_date=?, due_date_raw=?, po_reference=?, revision=?, stated_subtotal=?, stated_tax=?,"
        " stated_charges=?, stated_total=?, updated_at=datetime('now') WHERE id=?",
        (ex.invoice_number, ex.vendor_name, ex.currency, ex.invoice_date, ex.due_date,
         ex.due_date_raw, ex.po_reference, ex.revision, ex.stated_subtotal, ex.stated_tax,
         charges, ex.stated_total, invoice_id),
    )
    for li in ex.line_items:
        uow.execute(
            "INSERT INTO invoice_line_items (invoice_id, item_raw, quantity, unit_price, line_total, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_id, li.item_raw, li.quantity, li.unit_price, li.line_total, li.note),
        )


def _enum_val(x: object) -> object:
    return x.value if isinstance(x, Enum) else x


def record_findings(uow: UnitOfWork, invoice_id: int, findings: list, source: str) -> None:
    """Append findings (deterministic Finding objects or the judge's concerns).
    Each carries .code, .severity, .message, .details — enums or plain strings."""
    for f in findings:
        uow.execute(
            "INSERT INTO findings (invoice_id, code, severity, message, details, source)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_id, _enum_val(f.code), _enum_val(f.severity), f.message,
             json.dumps(getattr(f, "details", {}) or {}), source),
        )


def save_validation(uow: UnitOfWork, invoice_id: int, result) -> None:
    """Persist the deterministic match: resolved vendor, fingerprint, the catalog
    identity matched onto each line, and the findings."""
    uow.execute(
        "UPDATE invoices SET vendor_id=?, fingerprint=?, updated_at=datetime('now') WHERE id=?",
        (result.vendor_id, result.fingerprint, invoice_id))
    for m in result.line_matches:
        if m.matched_item is not None:
            uow.execute("UPDATE invoice_line_items SET matched_item=?, matched_po_line_id=? WHERE id=?",
                        (m.matched_item, m.po_line_id, m.line_id))
    record_findings(uow, invoice_id, result.findings, source="deterministic")


def save_verdict(uow: UnitOfWork, invoice_id: int, verdict) -> None:
    """Persist the judge's advisory: pay/hold, the headline category + level, the
    human-readable summary, and any qualitative concerns as findings."""
    uow.execute(
        "UPDATE invoices SET recommendation=?, review_category=?, review_level=?, review_summary=?,"
        " updated_at=datetime('now') WHERE id=?",
        ("pay" if verdict.pay else "hold", _enum_val(verdict.review_category),
         _enum_val(verdict.level), verdict.summary, invoice_id))
    record_findings(uow, invoice_id, verdict.concerns, source="llm")


def set_outcome(uow: UnitOfWork, invoice_id: int, outcome: str, *, superseded_by: int | None = None) -> None:
    uow.execute(
        "UPDATE invoices SET outcome=?, superseded_by=?, updated_at=datetime('now') WHERE id=?",
        (outcome, superseded_by, invoice_id))


def apply_po_drawdown(uow: UnitOfWork, invoice_id: int) -> dict:
    """Consume the matched PO lines on payment: add each paid line's quantity to
    its PO line's qty_invoiced, then close any PO now fully drawn down. This is
    what makes over-billing *across* invoices catchable, and retires a fulfilled
    PO so later invoices against it surface as unauthorized. Returns a summary
    for the trace."""
    lines = uow.query(
        "SELECT matched_po_line_id, quantity FROM invoice_line_items"
        " WHERE invoice_id=? AND matched_po_line_id IS NOT NULL AND quantity IS NOT NULL",
        (invoice_id,))
    touched_pos: set[int] = set()
    for ln in lines:
        uow.execute("UPDATE po_lines SET qty_invoiced = qty_invoiced + ? WHERE id=?",
                    (ln["quantity"], ln["matched_po_line_id"]))
        touched_pos.add(uow.query("SELECT po_id FROM po_lines WHERE id=?",
                                  (ln["matched_po_line_id"],))[0]["po_id"])

    closed: list[str] = []
    for po_id in touched_pos:
        open_lines = uow.query(
            "SELECT COUNT(*) AS n FROM po_lines WHERE po_id=? AND qty_invoiced < qty_ordered",
            (po_id,))[0]["n"]
        if open_lines == 0:
            uow.execute("UPDATE purchase_orders SET status='closed' WHERE id=?", (po_id,))
            closed.append(uow.query("SELECT po_number FROM purchase_orders WHERE id=?",
                                    (po_id,))[0]["po_number"])
    return {"lines_drawn": len(lines), "pos_closed": closed}


def set_review_category(uow: UnitOfWork, invoice_id: int, category) -> None:
    """Gate fallback: stamp a deterministic category when the judge held an
    invoice without naming one (it should, but the gate must never leave a held
    invoice uncategorized)."""
    uow.execute("UPDATE invoices SET review_category=?, updated_at=datetime('now') WHERE id=?",
                (_enum_val(category), invoice_id))


def load_invoice(uow: UnitOfWork, invoice_id: int) -> dict:
    """The full invoice for an API/CLI response: row + line items + findings + trace."""
    rows = uow.query("SELECT * FROM invoices WHERE id=?", (invoice_id,))
    if not rows:
        impossible("load of a missing invoice", {"invoice_id": invoice_id})
    return {
        "invoice": dict(rows[0]),
        "line_items": [dict(r) for r in uow.query(
            "SELECT item_raw, matched_item, quantity, unit_price, line_total, note FROM invoice_line_items"
            " WHERE invoice_id=? ORDER BY id", (invoice_id,))],
        "findings": [dict(r) for r in uow.query(
            "SELECT code, severity, message, source FROM findings WHERE invoice_id=? ORDER BY id",
            (invoice_id,))],
        "trace": [dict(r) for r in uow.query(
            "SELECT seq, stage, kind FROM invoice_trace WHERE invoice_id=? ORDER BY seq", (invoice_id,))],
    }
