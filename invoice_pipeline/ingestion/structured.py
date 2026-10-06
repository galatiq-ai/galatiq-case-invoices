"""Structured-document parsers. Fields are read by key: a missing key is genuinely absent."""

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from decimal import Decimal

from invoice_pipeline.ingestion.normalize import (
    clean_text,
    normalize_sku,
    parse_date,
    parse_money,
    parse_quantity,
)
from invoice_pipeline.model import Ingested, Invoice, LineItem, Repair, normalize_invoice_number


def _text(value) -> str | None:
    return None if value is None else clean_text(str(value))


def parse_json(text: str, source_path: str) -> Ingested:
    """Nested JSON invoice; raises ValueError on invalid JSON (the ingestion boundary catches)."""
    data = json.loads(text, parse_float=Decimal)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object at the top level")
    vendor = data.get("vendor")
    if isinstance(vendor, dict):
        data = {**data, "vendor": vendor.get("name")}
    return _build(data, data.get("line_items") or [], source_path, "json")


def _build(data: dict, rows: list[dict], source_path: str, source_format: str) -> Ingested:
    """Map a format-neutral field dict (JSON key names) and line rows to a typed Invoice."""
    repairs: list[Repair] = []

    def money(key: str, *aliases: str) -> Decimal | None:
        raw = next((data[k] for k in (key, *aliases) if data.get(k) is not None), None)
        return parse_money(
            raw if raw is None or isinstance(raw, int | Decimal) else str(raw), key, repairs
        )

    items = []
    for n, row in enumerate(rows):
        sku, note = normalize_sku(_text(row.get("item")))
        notes = [x for x in (note, _text(row.get("note"))) if x]
        quantity = row.get("quantity")
        items.append(
            LineItem(
                raw_name=_text(row.get("item")) or "",
                sku=sku,
                raw_quantity=None if quantity is None else str(quantity),
                quantity=parse_quantity(
                    quantity if isinstance(quantity, int | Decimal) else _text(quantity)
                ),
                unit_price=parse_money(row.get("unit_price"), f"items[{n}].unit_price", repairs),
                line_total=parse_money(row.get("amount"), f"items[{n}].line_total", repairs),
                note="; ".join(notes) or None,
            )
        )

    invoice = Invoice(
        invoice_number=normalize_invoice_number(_text(data.get("invoice_number"))),
        vendor=_text(data.get("vendor")),
        revision=_text(data.get("revision")),
        invoice_date=parse_date(_us_date(_text(data.get("date"))), "invoice_date", repairs),
        due_date_text=_text(data.get("due_date")),
        payment_terms=_text(data.get("payment_terms")),
        currency=(_text(data.get("currency")) or "USD").upper(),
        items=items,
        subtotal=money("subtotal"),
        tax=money("tax_amount", "tax"),
        shipping=money("shipping"),
        total=money("total"),
        notes=_text(data.get("notes")),
        po_reference=_text(data.get("po_reference") or data.get("po_number")),
        source_path=source_path,
        source_format=source_format,
    )
    return Ingested(invoice=invoice, findings=[], repairs=repairs)


def _us_date(text: str | None) -> str | None:
    """MM/DD/YYYY (the CSV spelling) as ISO; any other text is left for parse_date."""
    if text and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", text):
        try:
            return datetime.strptime(text, "%m/%d/%Y").date().isoformat()
        except ValueError:
            return text
    return text


_ROW_COLUMNS = {
    "invoice number": "invoice_number",
    "vendor": "vendor",
    "date": "date",
    "due date": "due_date",
    "item": "item",
    "qty": "quantity",
    "unit price": "unit_price",
    "line total": "amount",
    "currency": "currency",
}
_IDENTITY = ("invoice_number", "vendor", "date", "due_date", "currency")


def _footer_label(cell: str) -> str | None:
    """'Subtotal', 'Tax (6%):', 'TOTAL' ... as the footer field name, or None."""
    label = re.sub(r"\(.*?\)|:", "", cell).strip().lower()
    return label if label in ("subtotal", "tax", "total") else None


def parse_csv(text: str, source_path: str) -> list[Ingested]:
    """CSV in two shapes: field/value rows (repeated item groups) or one row per line item.

    Row shape groups rows by Invoice Number: a blank number on a row with line content continues
    the invoice above; a footer row (Subtotal/Tax/Total label in any column, amount in the last
    numeric cell) belongs to the invoice above. Each invoice number becomes its own invoice.
    """
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("no CSV rows")
    header = [c.strip().lower() for c in rows[0]]
    if header[:2] == ["field", "value"]:
        data: dict = {}
        lines: list[dict] = []
        for row in rows[1:]:
            key, value = row[0].strip().lower(), row[1] if len(row) > 1 else ""
            if key == "item":
                lines.append({"item": value})
            elif key in ("quantity", "unit_price") and lines:
                lines[-1][key] = value
            else:
                data[key] = value
        return [_build(data, lines, source_path, "csv")]
    cols = {_ROW_COLUMNS[h]: i for i, h in enumerate(header) if h in _ROW_COLUMNS}
    groups: dict[str | None, tuple[dict, list[dict]]] = {}  # invoice number -> (fields, lines)
    current: str | None = None
    for row in rows[1:]:
        cells = {k: row[i].strip() if i < len(row) else "" for k, i in cols.items()}
        has_line = any(
            cells.get(k) and not _footer_label(cells[k]) for k in ("item", "quantity", "unit_price")
        )
        label = next((x for c in row if (x := _footer_label(c))), None)
        if not has_line and label:
            if groups:
                amounts = [c.strip() for c in row if re.fullmatch(r"[^A-Za-z]*\d[^A-Za-z]*", c)]
                groups[current][0][label] = amounts[-1] if amounts else None
            continue
        if not has_line:
            continue
        if cells.get("invoice_number"):
            current = cells["invoice_number"]
        data, lines = groups.setdefault(current, ({}, []))
        for key in _IDENTITY:  # blank cells inherit the identity from the rows above
            if cells.get(key) and not data.get(key):
                data[key] = cells[key]
        lines.append(cells)
    if not groups:
        raise ValueError("no CSV line rows")
    return [_build(data, lines, source_path, "csv") for data, lines in groups.values()]


def parse_xml(text: str, source_path: str) -> Ingested:
    """Nested XML invoice (header, line_items, totals). Stdlib expat does not fetch external
    entities; a malformed document raises ValueError for the ingestion boundary to catch."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"invalid XML: {exc}") from exc
    data: dict = {}
    for section in (root, root.find("header"), root.find("totals")):
        for child in section if section is not None else ():
            if len(child) == 0:
                data[child.tag] = child.text
    lines = [
        {
            "item": item.findtext("name"),
            "quantity": item.findtext("quantity"),
            "unit_price": item.findtext("unit_price"),
            "amount": item.findtext("amount"),
            "note": item.findtext("note"),
        }
        for item in root.findall("line_items/item")
    ]
    return _build(data, lines, source_path, "xml")
