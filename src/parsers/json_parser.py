"""JSON invoice parser.

Loads the JSON file and converts it to a normalised text representation.
If a `revision` field is present (e.g. invoice_1004_revised.json), it is
emitted as "[NOTE: this is a revised invoice]" in the text so the LLM
includes it in extraction_warnings, which validation_agent._is_revision()
then checks.
"""

import json
import logging

logger = logging.getLogger(__name__)


def parse_json(file_path: str) -> str:
    """Parse a JSON invoice file and return a normalised text block.

    Args:
        file_path: Path to the .json file.

    Returns:
        Normalised text representation, or empty string on failure.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        logger.error("File not found: %s", file_path)
        return ""
    except json.JSONDecodeError as exc:
        logger.error("JSON decode error for %s: %s", file_path, exc)
        return ""
    except Exception as exc:
        logger.error("Unexpected error reading %s: %s", file_path, exc)
        return ""

    lines: list[str] = []

    # Header fields
    inv_num = data.get("invoice_number", "")
    revision = data.get("revision", "")
    if inv_num:
        lines.append(f"invoice_number: {inv_num}")
    if revision:
        lines.append(f"revision: {revision}  [NOTE: this is a revised invoice]")

    vendor = data.get("vendor", {})
    if isinstance(vendor, dict):
        vendor_name = vendor.get("name", "")
        vendor_address = vendor.get("address", "")
    else:
        vendor_name = str(vendor)
        vendor_address = ""
    if vendor_name:
        lines.append(f"vendor: {vendor_name}")
    if vendor_address:
        lines.append(f"vendor_address: {vendor_address}")

    for field in ("date", "due_date", "currency", "payment_terms"):
        val = data.get(field)
        if val is not None:
            lines.append(f"{field}: {val}")

    notes = data.get("notes", "")
    if notes:
        lines.append(f"notes: {notes}")

    # Line items
    line_items = data.get("line_items", [])
    if line_items:
        lines.append("Items:")
        for item in line_items:
            name = item.get("item", item.get("name", "?"))
            qty = item.get("quantity", item.get("qty", "?"))
            unit_price = item.get("unit_price", "")
            amount = item.get("amount", "")
            note = item.get("note", "")
            row = f"  {name}  qty: {qty}"
            if unit_price:
                row += f"  unit_price: {unit_price}"
            if amount:
                row += f"  amount: {amount}"
            if note:
                row += f"  [{note}]"
            lines.append(row)

    # Totals
    for field in ("subtotal", "tax_rate", "tax_amount", "total"):
        val = data.get(field)
        if val is not None:
            lines.append(f"{field}: {val}")

    return "\n".join(lines)
