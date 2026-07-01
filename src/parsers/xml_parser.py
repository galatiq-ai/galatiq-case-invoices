"""XML invoice parser using stdlib xml.etree.ElementTree."""

import logging
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


def _text(element: ET.Element | None, default: str = "") -> str:
    """Safe text extraction from an XML element."""
    if element is None or element.text is None:
        return default
    return element.text.strip()


def parse_xml(file_path: str) -> str:
    """Parse an XML invoice file and return a normalised text block.

    Args:
        file_path: Path to the .xml file.

    Returns:
        Normalised text representation ready for the ingestion LLM, or
        empty string on failure.
    """
    try:
        tree = ET.parse(file_path)
    except FileNotFoundError:
        logger.error("File not found: %s", file_path)
        return ""
    except ET.ParseError as exc:
        logger.error("XML parse error for %s: %s", file_path, exc)
        return ""
    except Exception as exc:
        logger.error("Unexpected error reading %s: %s", file_path, exc)
        return ""

    root = tree.getroot()
    lines: list[str] = []

    header = root.find("header")
    if header is not None:
        if inv_num := _text(header.find("invoice_number")):
            lines.append(f"invoice_number: {inv_num}")
        if vendor := _text(header.find("vendor")):
            lines.append(f"vendor: {vendor}")
        if date := _text(header.find("date")):
            lines.append(f"date: {date}")
        if due_date := _text(header.find("due_date")):
            lines.append(f"due_date: {due_date}")
        if currency := _text(header.find("currency")):
            lines.append(f"currency: {currency}")

    line_items_el = root.find("line_items")
    if line_items_el is not None:
        lines.append("Items:")
        for item_el in line_items_el.findall("item"):
            name = _text(item_el.find("name"))
            qty = _text(item_el.find("quantity"))
            unit_price = _text(item_el.find("unit_price"))
            row = f"  {name}  qty: {qty}"
            if unit_price:
                row += f"  unit_price: {unit_price}"
            lines.append(row)

    totals = root.find("totals")
    if totals is not None:
        for field in ("subtotal", "tax_rate", "tax_amount", "total"):
            val = _text(totals.find(field))
            if val:
                lines.append(f"{field}: {val}")

    payment_terms = _text(root.find("payment_terms"))
    if payment_terms:
        lines.append(f"payment_terms: {payment_terms}")

    return "\n".join(lines)
