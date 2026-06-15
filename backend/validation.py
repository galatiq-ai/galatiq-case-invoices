"""Deterministic validation: the three-way match against vendor master + POs.

This layer decides only what is *verifiably wrong* — it compares the invoice
against ground truth (who we buy from, what we authorized, the document's own
arithmetic) and emits findings. It writes nothing and makes no decision; the
pipeline node persists the result and the gate reads it. Anything qualitative —
"this looks like a scam", "this reads like a re-bill" — is the judge's job, not
this module's.

An invoice is legitimate because we *ordered* it (a PO line authorizes the item,
quantity, and price from a known vendor), not because a name exists somewhere.
"""

import re
from dataclasses import dataclass, field
from sqlite3 import Row

from . import config
from .review import CODE_CATEGORY, Code, ReviewCategory, Severity
from .unit_of_work import UnitOfWork


@dataclass
class Finding:
    code: Code
    severity: Severity
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class LineMatch:
    line_id: int
    matched_item: str | None    # the PO line's item, once matched; None if unauthorized
    po_line_id: int | None = None  # the exact po_lines row matched, drawn down on payment


@dataclass
class ValidationResult:
    vendor_id: int | None
    fingerprint: str
    findings: list[Finding]
    line_matches: list[LineMatch]
    duplicate_of: int | None

    @property
    def blocking(self) -> bool:
        return any(f.severity.blocks for f in self.findings)

    @property
    def suggested_categories(self) -> list[ReviewCategory]:
        out: list[ReviewCategory] = []
        for f in self.findings:
            cat = CODE_CATEGORY.get(f.code)
            if cat is not None and cat not in out:
                out.append(cat)
        return out


def _norm(s: str | None) -> str:
    """Lowercase, drop everything but alphanumerics — so 'Widgets Inc.' and
    'widgets inc' (and 'WidgetA (rush)') compare equal on identity."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _money_eq(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def validate(uow: UnitOfWork, invoice_id: int) -> ValidationResult:
    inv = uow.query("SELECT * FROM invoices WHERE id=?", (invoice_id,))[0]
    lines = uow.query(
        "SELECT id, item_raw, quantity, unit_price, line_total FROM invoice_line_items"
        " WHERE invoice_id=? ORDER BY id", (invoice_id,))

    findings: list[Finding] = []
    vendor = _resolve_vendor(uow, inv["vendor_raw"], findings)
    vendor_id = vendor["id"] if vendor else None

    if vendor is not None:
        _check_currency(inv, vendor, findings)

    authorized = _authorized_lines(uow, vendor_id) if vendor_id is not None else {}
    if vendor_id is not None and not authorized:
        findings.append(Finding(
            Code.NO_PO, Severity.ERROR,
            f"no open purchase order on file for {vendor['name']}",
            {"vendor_id": vendor_id}))

    _check_negatives(lines, findings)
    line_matches = _match_lines(lines, authorized, findings)
    _check_line_arithmetic(lines, findings)
    _check_totals(inv, lines, findings)
    _check_data_integrity(inv, findings)
    _check_oversize(inv, findings)

    fingerprint = _fingerprint(inv)
    duplicate_of = _check_duplicate(uow, invoice_id, fingerprint, findings)

    return ValidationResult(vendor_id, fingerprint, findings, line_matches, duplicate_of)


def _resolve_vendor(uow: UnitOfWork, vendor_raw: str | None, findings: list[Finding]) -> Row | None:
    if not vendor_raw:
        findings.append(Finding(Code.MISSING_FIELD, Severity.ERROR, "invoice names no vendor"))
        return None
    target = _norm(vendor_raw)
    for v in uow.query("SELECT id, name, status, currency FROM vendors"):
        if _norm(v["name"]) == target:
            if v["status"] != "active":
                findings.append(Finding(
                    Code.VENDOR_INACTIVE, Severity.ERROR,
                    f"vendor {v['name']} is {v['status']}, not payable", {"vendor_id": v["id"]}))
            return v
    alias = uow.query(
        "SELECT vendor_id FROM vendor_aliases WHERE LOWER(alias)=LOWER(?)", (vendor_raw.strip(),))
    if alias:
        return uow.query("SELECT id, name, status, currency FROM vendors WHERE id=?",
                         (alias[0]["vendor_id"],))[0]
    findings.append(Finding(
        Code.UNKNOWN_VENDOR, Severity.ERROR,
        f"'{vendor_raw}' is not in the vendor master", {"vendor_raw": vendor_raw}))
    return None


def _check_currency(inv: Row, vendor: Row, findings: list[Finding]) -> None:
    if inv["currency"] and inv["currency"] != vendor["currency"]:
        findings.append(Finding(
            Code.CURRENCY_MISMATCH, Severity.WARNING,
            f"invoice is in {inv['currency']}, but {vendor['name']} bills in {vendor['currency']}",
            {"invoice": inv["currency"], "vendor": vendor["currency"]}))


def _authorized_lines(uow: UnitOfWork, vendor_id: int) -> dict[str, list[Row]]:
    """The vendor's authorized catalog: open-PO lines, keyed by normalized item."""
    rows = uow.query(
        "SELECT l.id, l.item, l.qty_ordered, l.qty_invoiced, l.unit_price"
        " FROM po_lines l JOIN purchase_orders p ON p.id = l.po_id"
        " WHERE p.vendor_id=? AND p.status='open'", (vendor_id,))
    catalog: dict[str, list[Row]] = {}
    for r in rows:
        catalog.setdefault(_norm(r["item"]), []).append(r)
    return catalog


def _match_lines(lines: list[Row], authorized: dict[str, list[Row]],
                 findings: list[Finding]) -> list[LineMatch]:
    matches: list[LineMatch] = []
    for line in lines:
        candidates = authorized.get(_norm(line["item_raw"]))
        if not candidates:
            # Only a real "unauthorized item" once the vendor has *some* catalog;
            # a vendor with no PO at all is already reported once as NO_PO.
            if authorized:
                findings.append(Finding(
                    Code.ITEM_NOT_ON_PO, Severity.ERROR,
                    f"'{line['item_raw']}' is on no open PO for this vendor",
                    {"item": line["item_raw"]}))
            matches.append(LineMatch(line["id"], None))
            continue
        po = candidates[0]
        matches.append(LineMatch(line["id"], po["item"], po["id"]))
        remaining = po["qty_ordered"] - po["qty_invoiced"]
        if line["quantity"] is not None and line["quantity"] > remaining:
            findings.append(Finding(
                Code.QTY_OVER_AUTHORIZED, Severity.ERROR,
                f"{line['item_raw']}: billed {line['quantity']}, only {remaining} authorized on the PO",
                {"item": line["item_raw"], "billed": line["quantity"], "authorized": remaining}))
        if line["unit_price"] is not None and not _money_eq(
                line["unit_price"], po["unit_price"], config.PRICE_TOLERANCE):
            findings.append(Finding(
                Code.PRICE_MISMATCH, Severity.ERROR,
                f"{line['item_raw']}: billed {line['unit_price']}, PO price is {po['unit_price']}",
                {"item": line["item_raw"], "billed": line["unit_price"], "authorized": po["unit_price"]}))
    return matches


def _check_line_arithmetic(lines: list[Row], findings: list[Finding]) -> None:
    for line in lines:
        q, up, lt = line["quantity"], line["unit_price"], line["line_total"]
        if q is not None and up is not None and lt is not None and not _money_eq(
                q * up, lt, config.MONEY_TOLERANCE):
            findings.append(Finding(
                Code.ARITHMETIC_MISMATCH, Severity.WARNING,
                f"{line['item_raw']}: {q} × {up} = {q * up:.2f}, but line reads {lt}",
                {"item": line["item_raw"]}))


def _check_totals(inv: Row, lines: list[Row], findings: list[Finding]) -> None:
    # Only reconcile against the line sum when every line states a total; a
    # partial sum is meaningless and would fire a false mismatch.
    have_line_totals = bool(lines) and all(line["line_total"] is not None for line in lines)
    line_sum = sum(line["line_total"] for line in lines if line["line_total"] is not None)
    subtotal = inv["stated_subtotal"]
    if subtotal is not None and have_line_totals and not _money_eq(
            line_sum, subtotal, config.MONEY_TOLERANCE):
        findings.append(Finding(
            Code.ARITHMETIC_MISMATCH, Severity.WARNING,
            f"line items sum to {line_sum:.2f}, stated subtotal is {subtotal}",
            {"line_sum": line_sum, "stated_subtotal": subtotal}))

    total = inv["stated_total"]
    base = subtotal if subtotal is not None else (line_sum if have_line_totals else None)
    if total is not None and base is not None:
        computed = base + (inv["stated_tax"] or 0) + (inv["stated_charges"] or 0)
        if not _money_eq(computed, total, config.MONEY_TOLERANCE):
            findings.append(Finding(
                Code.ARITHMETIC_MISMATCH, Severity.ERROR,
                f"subtotal + tax + charges = {computed:.2f}, but stated total is {total}",
                {"computed": computed, "stated_total": total}))


def _check_data_integrity(inv: Row, findings: list[Finding]) -> None:
    if inv["stated_total"] is None:
        findings.append(Finding(Code.MISSING_FIELD, Severity.WARNING, "no total stated on the invoice"))
    if inv["due_date"] is None and inv["due_date_raw"]:
        findings.append(Finding(
            Code.DUE_DATE_INVALID, Severity.WARNING,
            f"due date '{inv['due_date_raw']}' is not a real date", {"raw": inv["due_date_raw"]}))


def _check_oversize(inv: Row, findings: list[Finding]) -> None:
    total = inv["stated_total"]
    if total is not None and total > config.APPROVAL_THRESHOLD:
        findings.append(Finding(
            Code.OVERSIZE, Severity.ERROR,
            f"total {total} is over the {config.APPROVAL_THRESHOLD:.0f} auto-pay ceiling — needs sign-off",
            {"total": total, "threshold": config.APPROVAL_THRESHOLD}))


def _check_negatives(lines: list[Row], findings: list[Finding]) -> None:
    for line in lines:
        if line["quantity"] is not None and line["quantity"] < 0:
            findings.append(Finding(
                Code.NEGATIVE_QUANTITY, Severity.ERROR,
                f"{line['item_raw']}: negative quantity {line['quantity']}", {"item": line["item_raw"]}))
        for fld in ("unit_price", "line_total"):
            if line[fld] is not None and line[fld] < 0:
                findings.append(Finding(
                    Code.NEGATIVE_PRICE, Severity.ERROR,
                    f"{line['item_raw']}: negative {fld} {line[fld]}", {"item": line["item_raw"]}))


def _fingerprint(inv: Row) -> str:
    return "|".join((_norm(inv["vendor_raw"]), _norm(inv["invoice_number"]),
                     f"{inv['stated_total'] or 0:.2f}"))


def _check_duplicate(uow: UnitOfWork, invoice_id: int, fingerprint: str,
                     findings: list[Finding]) -> int | None:
    prior = uow.query(
        "SELECT id FROM invoices WHERE fingerprint=? AND id<>? AND status IN ('paid','approved')"
        " ORDER BY id LIMIT 1", (fingerprint, invoice_id))
    if not prior:
        return None
    findings.append(Finding(
        Code.DUPLICATE, Severity.ERROR,
        f"exact match of already-processed invoice #{prior[0]['id']}",
        {"duplicate_of": prior[0]["id"]}))
    return prior[0]["id"]
