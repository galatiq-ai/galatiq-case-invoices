"""Ingestion Agent — extracts structured data from raw invoice text.

Uses LLM-driven extraction with a deterministic regex fallback.
Confidence scoring determines which path to use.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from src.state import ExtractedInvoice, LineItem
from src.tools import read_file_content, write_trace, get_error_context
import src.config as config
from src.config import get_llm
from src.audit import record_stage_event


def ingest_node(state: dict) -> dict:
    """LangGraph node: ingest an invoice file and extract structured data.

    Uses LLM extraction (simulated via _llm_extract) with a deterministic
    regex fallback when LLM confidence is below 0.8.
    """
    file_path = state.get("file_path", "")
    if not file_path:
        state["error"] = "No file path provided"
        return state

    try:
        raw_text = read_file_content(file_path)
        state["raw_text"] = raw_text
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        state["error"] = get_error_context(e)
        return state

    # Read correction hints from a previous approval loop if any
    correction_hints = []
    prior_decision = state.get("approval_decision")
    if prior_decision and isinstance(prior_decision, dict):
        correction_hints = prior_decision.get("correction_hints", [])

    # Try LLM extraction first, passing any correction hints
    extracted = _llm_extract(raw_text, file_path, correction_hints=correction_hints)

    # If confidence is low, fall back to deterministic
    if extracted.confidence < 0.8:
        fallback = _deterministic_extract(raw_text, file_path)
        fallback.confidence = extracted.confidence  # keep the lower score
        fallback.extraction_method = "deterministic"
        fallback.ingestion_errors.append(
            f"LLM confidence {extracted.confidence:.2f} < 0.8, used deterministic fallback"
        )
        extracted = fallback

    state["extracted_invoice"] = extracted.model_dump()
    write_trace(state, "ingest_agent")

    print(f"  [INGEST] {extracted.invoice_number} | {extracted.vendor} | "
          f"${extracted.total:,.2f} | method={extracted.extraction_method} "
          f"conf={extracted.confidence:.2f}")

    if config.VERBOSE:
        print(f"    [INGEST DETAIL] Items extracted: {[li.item for li in extracted.items]}")
        if extracted.ingestion_errors:
            print(f"    [INGEST DETAIL] Errors: {extracted.ingestion_errors}")

    try:
        record_stage_event(
            invoice_number=extracted.invoice_number,
            file_name=Path(file_path).name,
            stage="ingest",
            status=("success" if not extracted.ingestion_errors else "failed"),
            reason=("; ".join(extracted.ingestion_errors) if extracted.ingestion_errors else ""),
            actor="ingest_agent",
            metadata={"confidence": float(extracted.confidence), "method": extracted.extraction_method},
            total=float(extracted.total or 0.0),
            vendor=extracted.vendor or "",
        )
    except Exception:
        # Best-effort: don't break pipeline on audit failures
        pass

    return state


def _llm_extract(raw_text: str, file_path: str, correction_hints: list[str] | None = None) -> ExtractedInvoice:
    """Call the configured LLM to extract structured invoice data.

    Returns an ExtractedInvoice. If the LLM is unavailable or returns
    unparseable output, falls back to deterministic extraction.
    """
    llm = get_llm()
    if llm is None:
        result = _deterministic_extract(raw_text, file_path)
        result.extraction_method = "deterministic"
        result.ingestion_errors.append("No LLM API key configured — used deterministic fallback")
        return result

    # Read the ingestion prompt from docs/
    prompt_path = Path(__file__).resolve().parent.parent / "docs" / "prompt_templates.md"
    system_prompt = _read_ingestion_prompt(prompt_path)

    # Add correction hints if this is a re-ingestion pass
    hints_text = ""
    if correction_hints:
        hints_text = (
            "\n\nIMPORTANT — Previous extraction attempt was flagged. Correction hints:\n"
            + "\n".join(f"- {h}" for h in correction_hints)
            + "\nPlease address these issues in your extraction.\n"
        )

    user_message = f"Invoice text to extract:\n\n{raw_text}{hints_text}"

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from src.llm_binding import invoke_model_with_schema

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]

        # We prefer structured binding when available; pass a simple schema hint
        schema_hint = {"fields": ["invoice_number", "vendor", "date", "due_date", "items", "total", "currency", "confidence"]}

        data = None
        try:
            data = invoke_model_with_schema(llm, messages, schema=schema_hint)
        except Exception as e:
            # If binding/invoke failed, try a plain invoke and parse as text
            try:
                resp = llm.invoke(messages)
                data = json.loads((getattr(resp, "content", str(resp))).strip())
            except Exception:
                raise

        # If the helper returned raw text, attempt JSON parse
        if isinstance(data, str):
            data = json.loads(data)

        items = []
        for li in (data or {}).get("items", []):
            items.append(LineItem(
                item=_parse_item_name(li.get("item", "")),
                qty=int(li.get("qty", li.get("quantity", 0)) or 0),
                unit_price=float(li.get("unit_price", li.get("price", 0) or 0)),
                note=li.get("note"),
            ))

        return ExtractedInvoice(
            invoice_number=str((data or {}).get("invoice_number", "unknown")),
            vendor=(data or {}).get("vendor", "Unknown Vendor") or "Unknown Vendor",
            date=(data or {}).get("date"),
            due_date=(data or {}).get("due_date"),
            items=items,
            total=float((data or {}).get("total", 0) or 0),
            currency=(data or {}).get("currency", "USD"),
            raw_text=raw_text,
            extraction_method="llm",
            confidence=float((data or {}).get("confidence", 0.9) or 0.9),
        )

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        fallback = _deterministic_extract(raw_text, file_path)
        fallback.ingestion_errors.append(f"LLM response parse error ({type(e).__name__}): {e}")
        return fallback
    except Exception as e:
        fallback = _deterministic_extract(raw_text, file_path)
        fallback.ingestion_errors.append(f"LLM call failed ({type(e).__name__}): {e}")
        return fallback


def _read_ingestion_prompt(prompt_path: Path) -> str:
    """Read the ingestion section from docs/prompt_templates.md."""
    if not prompt_path.exists():
        return (
            "Extract structured invoice data from the raw text. "
            "Return JSON with: invoice_number, vendor, date, due_date, "
            "items (array of {item, qty, unit_price}), total, currency. "
            "Output valid JSON only."
        )
    content = prompt_path.read_text(encoding="utf-8")
    match = re.search(r"## Ingestion Prompt\n(.+?)(?=\n## |\Z)", content, re.DOTALL)
    return match.group(1).strip() if match else content



def _deterministic_extract(raw_text: str, file_path: str) -> ExtractedInvoice:
    """Deterministic regex-based extraction with heuristics.

    Handles all 5 formats (TXT, JSON, CSV, XML, PDF) and the edge cases
    found in the invoice data.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".json":
        return _extract_json(raw_text, path.stem)
    elif ext == ".csv":
        return _extract_csv(raw_text)
    elif ext == ".xml":
        return _extract_xml(raw_text)
    else:
        return _extract_text(raw_text, path.stem)


def _parse_currency_amount(text: str) -> float:
    """Parse a currency string like '$1,500.00' or '1500' to float."""
    text = text.strip()
    # Handle OCR artifacts: 'O' vs '0'
    text = text.replace("O", "0").replace("o", "0")
    text = re.sub(r"[^0-9.\-]", "", text)
    try:
        return float(text)
    except ValueError:
        return 0.0


def _parse_item_name(name: str) -> str:
    """Normalize item names: 'Widget A' → 'WidgetA', 'Gadget X' → 'GadgetX'."""
    name = name.strip()
    # Remove extra spaces within name
    name = re.sub(r'\s+', '', name)
    return name


def _resolve_relative_date(text: str) -> Optional[str]:
    """Resolve relative date strings like 'yesterday' to absolute dates."""
    today = date.today()
    orig = text.strip()
    text = orig.lower()
    if text == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if text == "today":
        return today.isoformat()
    if text == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    # Try standard formats
    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%d-%b-%Y", "%b %d %Y"]:
        try:
            return datetime.strptime(orig, fmt).date().isoformat()
        except ValueError:
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
    # Handle OCR: 26-Jan-2O26 → fix O/o → 0, then parse
    fixed = orig.replace("O", "0").replace("o", "0")
    for fmt in ["%d-%b-%Y", "%d-%B-%Y"]:
        try:
            return datetime.strptime(fixed, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _extract_json(raw_text: str, filename: str) -> ExtractedInvoice:
    """Extract from JSON-format invoice."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return _extract_text(raw_text, filename)

    inv_num = data.get("invoice_number", data.get("invoice", filename))

    # Handle nested vendor
    vendor_data = data.get("vendor", {})
    if isinstance(vendor_data, dict):
        vendor = vendor_data.get("name", "")
    else:
        vendor = str(vendor_data) if vendor_data else ""

    date_str = data.get("date")
    due_date = data.get("due_date")

    currency = data.get("currency", "USD")

    items = []
    for li in data.get("line_items", data.get("items", [])):
        if isinstance(li, dict):
            item_name = _parse_item_name(li.get("item", ""))
            items.append(LineItem(
                item=item_name,
                qty=int(li.get("quantity", li.get("qty", 0))),
                unit_price=float(li.get("unit_price", li.get("price", 0) or 0)),
                amount=float(li.get("amount", 0) or 0),
                note=li.get("note"),
            ))

    total = float(data.get("total", data.get("total_amount", 0) or 0))

    errors = []
    if not vendor:
        errors.append("Missing vendor name")
    if not inv_num:
        errors.append("Missing invoice number")

    return ExtractedInvoice(
        invoice_number=str(inv_num),
        vendor=vendor or "Unknown Vendor",
        date=date_str,
        due_date=due_date,
        items=items,
        total=total,
        currency=currency,
        raw_text=raw_text,
        extraction_method="deterministic",
        confidence=_compute_confidence(inv_num, vendor, items, total, raw_text),
        ingestion_errors=errors,
    )


def _extract_csv(raw_text: str) -> ExtractedInvoice:
    """Extract from CSV-format invoice. Handles both key-value and tabular."""
    lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]

    if not lines:
        return ExtractedInvoice(
            invoice_number="unknown",
            vendor="Unknown Vendor",
            raw_text=raw_text,
            extraction_method="deterministic",
            confidence=0.1,
            ingestion_errors=["Empty CSV"],
        )

    # Detect format: key-value (field,value) vs tabular (header row)
    first_line = lines[0]
    if first_line.startswith("field,") or first_line.startswith("field,"):
        return _extract_csv_keyvalue(lines, raw_text)
    else:
        return _extract_csv_tabular(lines, raw_text)


def _extract_csv_keyvalue(lines: list[str], raw_text: str) -> ExtractedInvoice:
    """Extract from key-value CSV like INV-1006."""
    data = {}
    items = []
    seen_items_section = False
    current_item = {}

    for line in lines:
        parts = line.split(",", 1)
        if len(parts) == 2:
            key = parts[0].strip().lower()
            value = parts[1].strip()
            if key == "item":
                if current_item:
                    items.append(current_item)
                current_item = {"item": value}
                seen_items_section = True
            elif key == "quantity":
                current_item["qty"] = int(_parse_currency_amount(value))
            elif key == "unit_price":
                current_item["unit_price"] = _parse_currency_amount(value)
            elif not seen_items_section:
                data[key] = value

    if current_item:
        items.append(current_item)

    line_items = []
    for it in items:
        line_items.append(LineItem(
            item=_parse_item_name(it.get("item", "")),
            qty=it.get("qty", 0),
            unit_price=it.get("unit_price"),
        ))

    total = _parse_currency_amount(data.get("total", "0"))

    return ExtractedInvoice(
        invoice_number=data.get("invoice_number", "unknown"),
        vendor=data.get("vendor", "Unknown Vendor"),
        date=data.get("date"),
        due_date=data.get("due_date"),
        items=line_items,
        total=total,
        raw_text=raw_text,
        extraction_method="deterministic",
        confidence=_compute_confidence(
            data.get("invoice_number", ""),
            data.get("vendor", ""),
            line_items,
            total,
            raw_text,
        ),
    )


def _extract_csv_tabular(lines: list[str], raw_text: str) -> ExtractedInvoice:
    """Extract from tabular CSV with header row (INV-1007, INV-1015)."""
    header = [h.strip().lower() for h in lines[0].split(",")]
    items = []
    data_rows = []
    vendor = "Unknown Vendor"
    invoice_number = "unknown"
    date_str = None
    due_date = None
    total = 0.0

    # Column name mappings (handle variations)
    col_map = {
        "invoice number": "invoice_number",
        "invoice_number": "invoice_number",
        "inv #": "invoice_number",
        "vendor": "vendor",
        "date": "date",
        "due date": "due_date",
        "due_date": "due_date",
        "item": "item",
        "qty": "qty",
        "quantity": "qty",
        "unit price": "unit_price",
        "line total": "amount",
        "line_total": "amount",
        "amount": "amount",
        "notes": "note",
    }

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        row_data = {}
        for i, h in enumerate(header):
            if i < len(parts):
                mapped = col_map.get(h, h)
                row_data[mapped] = parts[i]

        # Detect summary rows (Subtotal, Tax, Total)
        row_type = row_data.get("item", "").lower()
        if row_type in ("subtotal:", "tax (6%):", "tax (0%):", "total:", "subtotal", "tax", "total"):
            if "total:" in row_type or row_type == "total":
                total = _parse_currency_amount(row_data.get("unit_price", "0"))
            continue

        # Extract header-level fields from first data row
        if not data_rows:
            invoice_number = row_data.get("invoice_number", invoice_number)
            vendor = row_data.get("vendor", vendor)
            date_str = row_data.get("date", date_str)
            due_date = row_data.get("due_date", due_date)

        item_name = _parse_item_name(row_data.get("item", ""))
        if item_name:
            items.append(LineItem(
                item=item_name,
                qty=int(_parse_currency_amount(row_data.get("qty", "0"))),
                unit_price=_parse_currency_amount(row_data.get("unit_price", "0")),
                amount=_parse_currency_amount(row_data.get("amount", "0")),
            ))
        data_rows.append(row_data)

    return ExtractedInvoice(
        invoice_number=invoice_number,
        vendor=vendor,
        date=date_str,
        due_date=due_date,
        items=items,
        total=total,
        raw_text=raw_text,
        extraction_method="deterministic",
        confidence=_compute_confidence(invoice_number, vendor, items, total, raw_text),
    )


def _extract_xml(raw_text: str) -> ExtractedInvoice:
    """Extract from XML-format invoice (INV-1014)."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(raw_text)
    except ET.ParseError:
        return ExtractedInvoice(
            invoice_number="unknown",
            vendor="Unknown Vendor",
            raw_text=raw_text,
            extraction_method="deterministic",
            confidence=0.1,
            ingestion_errors=["XML parse error"],
        )

    def _find_text(parent, *tags):
        for tag in tags:
            el = parent.find(tag)
            if el is not None and el.text:
                return el.text.strip()
        return ""

    header = root.find("header")
    header_vendor = _find_text(root, "header/vendor", "vendor")
    inv_num = _find_text(root, "header/invoice_number", "invoice_number")
    date_str = _find_text(root, "header/date", "date")
    due_date = _find_text(root, "header/due_date", "due_date")
    currency = _find_text(root, "header/currency", "currency") or "USD"

    items = []
    items_container = root.find("line_items")
    if items_container is None:
        items_container = root
    for item_el in items_container.findall("item"):
        name = _parse_item_name(_find_text(item_el, "name"))
        qty_str = _find_text(item_el, "quantity", "qty")
        price_str = _find_text(item_el, "unit_price", "price")
        items.append(LineItem(
            item=name,
            qty=int(qty_str) if qty_str else 0,
            unit_price=float(price_str) if price_str else None,
        ))

    totals = root.find("totals")
    if totals is not None:
        total_str = _find_text(totals, "total")
    else:
        total_str = _find_text(root, "total")

    total = float(total_str) if total_str else 0.0

    return ExtractedInvoice(
        invoice_number=inv_num or "unknown",
        vendor=header_vendor or "Unknown Vendor",
        date=date_str,
        due_date=due_date,
        items=items,
        total=total,
        currency=currency,
        raw_text=raw_text,
        extraction_method="deterministic",
        confidence=_compute_confidence(inv_num, header_vendor, items, total, raw_text),
    )


def _strip_email_headers(text: str) -> str:
    """Strip email headers from an invoice formatted as an email body.

    Detects email format by finding lines starting with From:/To:/Subject:
    in the first 10 lines. If detected, discards all header lines up to
    the first blank line (the standard header/body separator).
    """
    lines = text.split("\n")
    email_header_pattern = re.compile(
        r"^(From|To|Subject|Date|Cc|Bcc|Reply-To)\s*:", re.IGNORECASE
    )
    is_email = any(email_header_pattern.match(l.strip()) for l in lines[:10])
    if not is_email:
        return text  # Not an email — leave unchanged

    # Find the first blank line (separates headers from body)
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip() == "":
            body_start = i + 1
            break

    return "\n".join(lines[body_start:])


def _extract_text(raw_text: str, filename: str) -> ExtractedInvoice:
    """Extract from plain text invoices (TXT, or fallback for other formats).

    Handles multiple formats found in the data:
    - Structured TXT (INV-1001, INV-1011)
    - Messy/typo TXT (INV-1002, INV-1012)
    - Email body (INV-1008)
    - Multi-item with extra fields (INV-1010)
    """
    # Pre-pass: strip email headers if formatted as an email body
    raw_text = _strip_email_headers(raw_text)

    invoice_number = "unknown"
    vendor = "Unknown Vendor"
    date_str = None
    due_date = None
    items = []
    total = 0.0
    currency = "USD"

    lines = raw_text.split("\n")

    # Pass 1: extract header fields using flexible patterns
    for line in lines:
        line_lower = line.strip().lower()

        # Invoice number patterns
        m = re.search(r'(?:invoice\s*(?:#|number|no)[:\s#]*|inv\s*(?:no|#)[:\s]*)\s*([\w\-]+)', line_lower)
        if m and invoice_number == "unknown":
            invoice_number = m.group(1).upper()

        # Vendor/From patterns
        m = re.search(r'(?:vendor|from|vndr|bill\s*from)[:\s]+(.+)', line_lower)
        if m:
            vendor = m.group(1).strip().title()

        # Date patterns
        m = re.search(r'(?:date|dt)[:\s]+(.+)', line_lower, re.IGNORECASE)
        if m and not date_str:
            parsed = m.group(1).strip()
            resolved = _resolve_relative_date(parsed)
            if resolved:
                date_str = resolved

        # Due date patterns
        m = re.search(r'(?:due\s*date|due\s*dt|due)[:\s]+(.+)', line_lower)
        if m and not due_date:
            parsed = m.group(1).strip()
            resolved = _resolve_relative_date(parsed)
            if resolved:
                due_date = resolved

        # Total patterns
        m = re.search(r'(?:total\s*amount|total|grand\s*total|amount|amt)[:\s]*\$?([0-9,]+\.?\d*)', line_lower)
        if m and total == 0.0:
            total = _parse_currency_amount(m.group(1))

        # Currency detection
        if "eur" in line_lower:
            currency = "EUR"

    # Pass 2: extract line items from table-like sections
    in_items_section = False
    for line in lines:
        line_stripped = line.strip()

        # Detect item section start
        if re.search(r'item|description', line_stripped, re.IGNORECASE) and re.search(r'qty|quantity', line_stripped, re.IGNORECASE):
            in_items_section = True
            continue

        if not in_items_section:
            continue

        # Skip separators and summary lines
        if re.match(r'^[\s\-_=]+$', line_stripped):
            continue
        if re.search(r'subtotal|tax|shipping|total', line_stripped, re.IGNORECASE):
            in_items_section = False
            continue

        # Try to match: item_name qty price amount (with optional notes)
        # Pattern: optional spaces, item name, numbers with $ signs
        item_match = re.match(
            r'([A-Za-z][A-Za-z\s\-/.()]+?)\s+'  # item name (lazy)
            r'(\d+)\s+'                            # qty
            r'\$?([0-9,]+\.?\d*)'                   # unit price
            r'(?:\s+\$?([0-9,]+\.?\d*))?',          # optional amount
            line_stripped,
        )
        if item_match:
            item_name = item_match.group(1).strip()
            # Normalize: remove parenthetical notes from item name
            note = None
            note_match = re.search(r'\((.+)\)', item_name)
            if note_match:
                note = note_match.group(1)
                item_name = item_name[:note_match.start()].strip()

            items.append(LineItem(
                item=_parse_item_name(item_name),
                qty=int(item_match.group(2)),
                unit_price=_parse_currency_amount(item_match.group(3)),
                amount=_parse_currency_amount(item_match.group(4)) if item_match.group(4) else None,
                note=note,
            ))

    # Fallback: handle bulleted lists like "- SuperGizmo x12 $400.00 each"
    if not items:
        for line in lines:
            s = line.strip()
            m = re.match(r'^[\-\u2022\*]\s*([A-Za-z0-9][A-Za-z0-9\-\s/.()]+?)\s+[xX]\s*(\d+)\s+\$?([0-9,]+\.?\d*)', s)
            if m:
                name = m.group(1).strip()
                qty = int(m.group(2))
                price = _parse_currency_amount(m.group(3))
                items.append(LineItem(
                    item=_parse_item_name(name),
                    qty=qty,
                    unit_price=price,
                    amount=price * qty if price else None,
                ))

    # Additional fallback: lines with 'qty' and '@' or 'unit price' tokens
    if not items:
        for line in lines:
            s = line.strip()
            # Patterns like: GadgetX  qty 20   @ $750 ea
            m1 = re.match(r'^([A-Za-z0-9][\w\s\-/.()]+?)\s+qty[:\s]*\s*(\d+)\s+@\s*\$?([0-9,]+\.?\d*)', s, re.IGNORECASE)
            # Patterns like: WidgetA    qty: 10    unit price: $250.00
            m2 = re.match(r'^([A-Za-z0-9][\w\s\-/.()]+?)\s+qty[:\s]*\s*(\d+)\s+unit\s+price[:\s]*\$?([0-9,]+\.?\d*)', s, re.IGNORECASE)
            # Patterns like: FakeItem   qty: 100   unit price: $1,000.00
            if m1 or m2:
                mm = m1 or m2
                name = mm.group(1).strip()
                qty = int(mm.group(2))
                price = _parse_currency_amount(mm.group(3))
                items.append(LineItem(
                    item=_parse_item_name(name),
                    qty=qty,
                    unit_price=price,
                    amount=price * qty if price else None,
                ))
    # Fix known issues
    if vendor == "Unknown Vendor" and "noprod" in raw_text.lower():
        vendor = "NoProd Industries"
    if vendor == "Unknown Vendor" and "widgets inc" in raw_text.lower():
        vendor = "Widgets Inc."

    return ExtractedInvoice(
        invoice_number=invoice_number,
        vendor=vendor,
        date=date_str,
        due_date=due_date,
        items=items,
        total=total,
        currency=currency,
        raw_text=raw_text,
        extraction_method="deterministic",
        confidence=_compute_confidence(invoice_number, vendor, items, total, raw_text),
    )


def _compute_confidence(invoice_number: str, vendor: str, items: list, total: float, raw_text: str) -> float:
    """Compute extraction confidence score 0.0-1.0."""
    score = 0.4  # base (conservative)

    if vendor and vendor != "Unknown Vendor":
        score += 0.2
    if invoice_number and invoice_number != "unknown":
        score += 0.1
    if items:
        score += 0.15
        if all(getattr(it, 'qty', 0) != 0 for it in items):
            score += 0.05
        if any(getattr(it, 'unit_price', None) is not None for it in items):
            score += 0.05
    if total > 0:
        score += 0.05
    else:
        score -= 0.05

    # Check for suspicious urgency language (lowers confidence more strongly)
    urgency_patterns = [
        r'urgent', r'pay\s*immediately', r'avoid\s*penalt',
        r'wire\s*transfer', r'immediate\s*payment',
    ]
    for pat in urgency_patterns:
        if re.search(pat, raw_text, re.IGNORECASE):
            score -= 0.25
            break

    # Check for relative dates (lowers confidence slightly)
    if re.search(r'yesterday|today|tomorrow', raw_text, re.IGNORECASE):
        score -= 0.05

    return max(0.0, min(1.0, score))
