"""CSV invoice parser.

Handles two CSV layouts found in the provided invoices:

  Vertical (key-value pairs):
    field,value
    invoice_number,INV-1006
    item,WidgetA
    quantity,5
    ...
  Used by invoice_1006.csv.

  Tabular (one row per line item):
    Invoice Number,Vendor,Date,Due Date,Item,Qty,Unit Price,Line Total
    INV-1007,...
  Used by invoice_1007.csv and invoice_1015.csv.

Both layouts are converted to a normalised string representation so the
ingestion LLM receives consistent input regardless of source format.
"""

import logging
import re
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _is_vertical(df: pd.DataFrame) -> bool:
    """Return True if the CSV has 'field' and 'value' columns (vertical layout)."""
    cols = [c.strip().lower() for c in df.columns]
    return "field" in cols and "value" in cols


def _parse_vertical(df: pd.DataFrame) -> str:
    """Convert a vertical key-value CSV to a normalised text block."""
    lines: list[str] = []
    item_groups: list[dict[str, Any]] = []
    current_item: dict[str, Any] = {}

    for _, row in df.iterrows():
        field = str(row.iloc[0]).strip().lower()
        value = str(row.iloc[1]).strip()

        if field == "item":
            if current_item:
                item_groups.append(current_item)
            current_item = {"item": value}
        elif field in ("quantity", "qty"):
            current_item["qty"] = value
        elif field == "unit_price":
            current_item["unit_price"] = value
        else:
            lines.append(f"{field}: {value}")

    if current_item:
        item_groups.append(current_item)

    if item_groups:
        lines.append("Items:")
        for grp in item_groups:
            parts = [f"  {grp.get('item', '?')}"]
            if "qty" in grp:
                parts.append(f"qty: {grp['qty']}")
            if "unit_price" in grp:
                parts.append(f"unit_price: {grp['unit_price']}")
            lines.append("  " + "  ".join(parts))

    return "\n".join(lines)


def _parse_tabular(df: pd.DataFrame) -> str:
    """Convert a tabular (one-row-per-item) CSV to a normalised text block."""
    # Normalise column names
    df.columns = [re.sub(r"\s+", "_", c.strip().lower()) for c in df.columns]

    lines: list[str] = []
    meta_written = False

    for _, row in df.iterrows():
        inv_num = str(row.get("invoice_number", "")).strip()
        if inv_num:
            if not meta_written:
                if "vendor" in row.index:
                    lines.append(f"vendor: {row['vendor']}")
                if "date" in row.index:
                    lines.append(f"date: {row['date']}")
                if "due_date" in row.index:
                    lines.append(f"due_date: {row['due_date']}")
                lines.append(f"invoice_number: {inv_num}")
                lines.append("Items:")
                meta_written = True

            item = str(row.get("item", "")).strip()
            qty = str(row.get("qty", "")).strip()
            unit_price = str(row.get("unit_price", "")).strip()
            line_total = str(row.get("line_total", "")).strip()
            if item:
                lines.append(
                    f"  {item}  qty: {qty}  unit_price: {unit_price}  line_total: {line_total}"
                )
        else:
            # Summary rows (Subtotal/Tax/Total) with no invoice number.
            # Emit clean "label: value" pairs by finding the last two non-empty
            # cells in the row (label then amount), avoiding raw column-name dumps
            # that would confuse the LLM into treating them as line items.
            non_empty = [
                str(row[col]).strip() for col in row.index if str(row[col]).strip()
            ]
            if len(non_empty) >= 2:
                label = non_empty[-2].rstrip(":").lower()
                lines.append(f"{label}: {non_empty[-1]}")
            elif len(non_empty) == 1:
                lines.append(non_empty[0])

    return "\n".join(lines)


def parse_csv(file_path: str) -> str:
    """Parse a CSV invoice file and return a normalised text representation.

    Args:
        file_path: Path to the .csv file.

    Returns:
        Normalised text block ready for the ingestion LLM, or empty string
        on failure.
    """
    try:
        df = pd.read_csv(file_path, dtype=str).fillna("")
        if _is_vertical(df):
            return _parse_vertical(df)
        return _parse_tabular(df)
    except FileNotFoundError:
        logger.error("File not found: %s", file_path)
        return ""
    except Exception as exc:
        logger.error("CSV parse error for %s: %s", file_path, exc)
        return ""
