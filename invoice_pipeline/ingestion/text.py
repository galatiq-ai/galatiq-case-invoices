"""TXT / PDF-text parser. Deterministic, offline, and tolerant of messy labels."""

import re
from typing import Literal

from invoice_pipeline.ingestion.normalize import (
    clean_text,
    normalize_sku,
    parse_date,
    parse_money,
    parse_quantity,
)
from invoice_pipeline.model import Ingested, Invoice, LineItem, Repair, normalize_invoice_number

# label name -> spelling variants. Every label needs a ":" or "#" after it.
_LABELS = {
    "revision": r"revision|rev",
    "due": r"due\s*date|due\s*dt|due",
    "date": r"date|dt",
    "vendor": r"vendor|vndr|vendr",
    "from": r"from",
    "number": r"(?:invoice|invoce|inv)\s*(?:(?:number|no\.?)\s*)?",
    "po": r"(?:po|purchase\s*order)\s*(?:(?:number|no\.?|ref(?:erence)?)\s*)?",
    "terms": r"(?:(?:payment|pymnt|pmt)\s*)?terms",
    "subtotal": r"sub\s*-?total",
    "tax": r"(?:sales\s*)?tax(?:\s*\([^)]*\))?",
    "shipping": r"shipping",
    "total": r"(?:grand\s*)?total(?:\s*amount)?|amt|amount",
    "currency": r"currency",
    "notes": r"notes?",
}
_LABEL = re.compile(
    r"(?<![A-Za-z])(?:"
    + "|".join(f"(?P<{name}>{pattern})" for name, pattern in _LABELS.items())
    + r")\s*[:#]+\s*",
    re.IGNORECASE,
)
_AMOUNT = r"[$€][\d,.Oo]+"
_BULLET = r"^\s*[-*•]?\s*"
_ITEM_PATTERNS = [  # tried in order; the first match wins
    # "A  qty: 10  unit price: $250.00" / "A  qty 20  @ $750 ea"
    rf"{_BULLET}(?P<name>.+?)\s+qty:?\s*(?P<qty>\S+)\s+(?:unit\s*price:?\s*|@\s*)?"
    rf"(?P<price>{_AMOUNT})(?:\s+(?:each|ea))?(?:\s+(?P<amount>{_AMOUNT}))?\s*$",
    # "- A  x12  $400.00 each"
    rf"{_BULLET}(?P<name>.+?)\s+x(?P<qty>\d\S*)\s+(?P<price>{_AMOUNT})"
    rf"(?:\s+(?:each|ea))?(?:\s+(?P<amount>{_AMOUNT}))?\s*$",
    # table row: "A  4  $300.00  $1,200.00  optional note"
    rf"^\s*(?P<name>[A-Za-z].*?)\s+(?P<qty>[^\s$€]+)\s+(?P<price>{_AMOUNT})"
    rf"(?:\s+(?P<amount>{_AMOUNT}))?(?:\s+(?P<note>\S.*?))?\s*$",
]
_ITEM_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _ITEM_PATTERNS]
_PO = re.compile(r"\bPO[-\s#]?\d[\w-]*", re.IGNORECASE)


def _scan(text: str) -> tuple[dict[str, str], list[re.Match]]:
    """First value per label, and the regex match of every line that is an item candidate."""
    fields: dict[str, str] = {}
    items: list[re.Match] = []
    rows = text.splitlines()
    i = 0
    while i < len(rows):
        row = rows[i]
        i += 1
        found = list(_LABEL.finditer(row))
        for k, m in enumerate(found):
            nxt = found[k + 1] if k + 1 < len(found) else None
            if m.lastgroup == "notes":  # notes run to the end of the line and continue below
                value = [row[m.end() :].strip()]
                while i < len(rows) and rows[i].strip() and not _LABEL.search(rows[i]):
                    value.append(rows[i].strip())
                    i += 1
                fields.setdefault("notes", " ".join(v for v in value if v))
                break
            value = row[m.end() : nxt.start() if nxt else len(row)].strip()
            if value:
                fields.setdefault(m.lastgroup, value)
        if not found:
            match = next((m for p in _ITEM_PATTERNS if (m := p.match(row))), None)
            if match:
                items.append(match)
    return fields, items


def parse_text(
    text: str, source_path: str, source_format: Literal["txt", "pdf"] = "txt"
) -> Ingested:
    fields, matches = _scan(text)
    repairs: list[Repair] = []
    invoice_date = parse_date(fields.get("date"), "invoice_date", repairs)

    items = []
    for n, m in enumerate(matches):
        sku, note = normalize_sku(m["name"])
        notes = [x for x in (note, clean_text(m.groupdict().get("note"))) if x]
        items.append(
            LineItem(
                raw_name=m["name"].strip(),
                sku=sku,
                raw_quantity=m["qty"],
                quantity=parse_quantity(m["qty"]),
                unit_price=parse_money(m["price"], f"items[{n}].unit_price", repairs),
                line_total=parse_money(m["amount"], f"items[{n}].line_total", repairs),
                note="; ".join(notes) or None,
            )
        )

    sender = fields.get("from")
    vendor = clean_text(fields.get("vendor") or (sender if sender and "@" not in sender else None))
    number = normalize_invoice_number(fields.get("number"))
    total = parse_money(fields.get("total"), "total", repairs)
    po = _PO.search(text)
    invoice = Invoice(
        invoice_number=number,
        vendor=vendor,
        revision=clean_text(fields.get("revision")),
        invoice_date=invoice_date,
        due_date_text=clean_text(fields.get("due")),
        payment_terms=clean_text(fields.get("terms")),
        currency=(clean_text(fields.get("currency")) or "USD").upper(),
        items=items,
        subtotal=parse_money(fields.get("subtotal"), "subtotal", repairs),
        tax=parse_money(fields.get("tax"), "tax", repairs),
        shipping=parse_money(fields.get("shipping"), "shipping", repairs),
        total=total,
        notes=clean_text(fields.get("notes")),
        po_reference=clean_text(fields.get("po")) or (po[0] if po else None),
        source_path=source_path,
        source_format=source_format,
    )
    missing = [
        name
        for name, absent in (
            ("vendor", vendor is None),
            ("invoice_number", number is None),
            ("total", total is None),
            ("items", not items),
        )
        if absent
    ]
    return Ingested(
        invoice=invoice, findings=[], repairs=repairs, raw_text=text, missing_required=missing
    )
