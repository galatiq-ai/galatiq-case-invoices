"""Stage 1: parse an invoice file and extract structured data via LLM.

Routes by extension to the right parser, then uses a structured-output LLM
call to extract vendor/amount/items/due_date. Runs a second pass on low-
confidence extractions and flags if vendor or amount disagree between runs.
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from config import EXTRACTION_CONFIDENCE_THRESHOLD, SCHEMA_ERROR_CONFIDENCE_CAP
from src.graph.state import ExtractedData, InvoiceState
from src.llm_client import get_llm
from src.parsers.csv_parser import parse_csv
from src.parsers.json_parser import parse_json
from src.parsers.pdf_parser import parse_pdf
from src.parsers.text_parser import parse_text
from src.parsers.xml_parser import parse_xml

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an invoice data extraction specialist.  Your job is to extract
structured fields from raw invoice text.  The data may contain typos, OCR
errors, missing fields, unusual date formats, and potentially fraudulent
content.  Extract what you can and report what you cannot.

Rules:
- Never hallucinate a field value.  If a value cannot be determined, output
  null for that field and add a human-readable note to extraction_warnings.
- Normalise item names to their canonical form where possible (e.g.
  "Widget A", "widget a", "WIDGET A", "WidgetA" -> "WidgetA"; "Gadget X" ->
  "GadgetX").  Do not normalise names you cannot confidently map.
- Quantities must be integers.  If a quantity is missing, negative, or
  non-numeric, set qty to null and add a warning.
- `amount` is the invoice's grand total / total due.  It may appear as
  "total", "grand_total", "invoice_total", "amount_due", or similar.
  Always extract the final payable figure into `amount`.
- unit_price values are floats in the invoice's stated currency.
- due_date: output in ISO 8601 format (YYYY-MM-DD) if parseable; otherwise
  use the raw string and add a warning about the format.
- currency: output as ISO 4217 code (e.g. "USD", "EUR").  Default to "USD"
  if not stated.
- extraction_confidence: a float 0–1 reflecting your overall certainty.
  Lower it when the source is garbled, fields are missing, or you had to
  make significant normalisation guesses.
"""

_ITEM_NORMALISATION_HINTS = {
    "widget a": "WidgetA",
    "widgeta": "WidgetA",
    "widget b": "WidgetB",
    "widgetb": "WidgetB",
    "gadget x": "GadgetX",
    "gadgetx": "GadgetX",
    "fakeitem": "FakeItem",
    "fake item": "FakeItem",
}


def _route_parser(file_path: str) -> str:
    """Return raw text from the appropriate parser based on file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return parse_pdf(file_path)
    elif ext == ".txt":
        return parse_text(file_path)
    elif ext == ".csv":
        return parse_csv(file_path)
    elif ext == ".json":
        return parse_json(file_path)
    elif ext == ".xml":
        return parse_xml(file_path)
    else:
        logger.warning("Unknown file extension %s for %s", ext, file_path)
        return parse_text(file_path)  # best-effort


def _extract_once(raw_text: str, schema_error: str | None = None) -> ExtractedData:
    """Call the LLM once and return a structured ExtractedData.

    If *schema_error* is provided (from a prior Pydantic ValidationError), it is
    included in the prompt so the model can self-correct its output format rather
    than repeating the same structural mistake.
    """
    llm = get_llm()
    structured_llm = llm.with_structured_output(ExtractedData)

    user_content = f"Extract all invoice fields from the following:\n\n{raw_text}"
    if schema_error:
        user_content += (
            f"\n\n---\nYour previous extraction attempt failed schema validation "
            f"with this error:\n{schema_error}\n"
            "Fix the structural issues and return a valid response."
        )

    messages = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]
    result: ExtractedData = structured_llm.invoke(messages)
    return result


def _normalise_item_names(data: ExtractedData) -> ExtractedData:
    """Post-process item names using deterministic hint table."""
    for item in data.items:
        name_lower = item.name.lower()
        mapped = _ITEM_NORMALISATION_HINTS.get(
            name_lower
        ) or _ITEM_NORMALISATION_HINTS.get(name_lower.replace(" ", ""))
        if mapped:
            item.name = mapped
    return data


def run_ingestion(state: InvoiceState) -> dict[str, Any]:
    """LangGraph node: parse the invoice file and extract structured data."""
    invoice_path = state.get("invoice_path", "")
    logger.info("Ingestion: processing %s", invoice_path)

    # Step 1: parse raw text from file
    try:
        raw_text = _route_parser(invoice_path)
    except Exception as exc:
        logger.error("Parser failed for %s: %s", invoice_path, exc)
        raw_text = ""

    if not raw_text.strip():
        logger.warning("Empty or unreadable file: %s", invoice_path)
        empty_data = ExtractedData(
            extraction_warnings=["unreadable file: no content extracted"]
        )
        return {
            "extracted_data": empty_data.model_dump(),
            "llm_calls": state.get("llm_calls", 0),
            "total_tokens": state.get("total_tokens", 0),
            "audit_log": [
                {
                    "stage": "extraction",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "note": "unreadable file",
                }
            ],
        }

    # Step 2: LLM extraction — with schema-error retry on ValidationError.
    # If the model returns structurally invalid output (wrong types, missing
    # required fields), we feed the exact Pydantic error message back so the
    # model can self-correct rather than repeating the same mistake.
    llm_calls = state.get("llm_calls", 0)
    total_tokens = state.get("total_tokens", 0)
    schema_error_hint: str | None = None

    try:
        extracted = _extract_once(raw_text)
        llm_calls += 1
        extracted = _normalise_item_names(extracted)
    except ValidationError as ve:
        logger.warning(
            "Ingestion: schema validation failed on first pass (%s), retrying with error feedback",
            invoice_path,
        )
        schema_error_hint = str(ve)
        try:
            extracted = _extract_once(raw_text, schema_error=schema_error_hint)
            llm_calls += 1
            extracted = _normalise_item_names(extracted)
            extracted.extraction_warnings.append(
                "Schema self-correction applied: first extraction attempt had validation errors."
            )
            extracted.extraction_confidence = min(
                extracted.extraction_confidence, SCHEMA_ERROR_CONFIDENCE_CAP
            )
        except Exception as exc2:
            logger.error("LLM extraction failed after schema-error retry: %s", exc2)
            extracted = ExtractedData(
                extraction_warnings=[
                    f"Extraction failed after schema-error retry: {exc2}",
                    f"Original schema error: {schema_error_hint[:200]}",
                ],
                extraction_confidence=0.0,
            )
            return {
                "extracted_data": extracted.model_dump(),
                "llm_calls": llm_calls,
                "total_tokens": total_tokens,
                "audit_log": [
                    {
                        "stage": "extraction",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "status": "error",
                        "note": f"schema retry failed: {exc2}",
                    }
                ],
            }
    except Exception as exc:
        logger.error("LLM extraction failed for %s: %s", invoice_path, exc)
        extracted = ExtractedData(
            extraction_warnings=[f"LLM extraction error: {exc}"],
            extraction_confidence=0.0,
        )
        return {
            "extracted_data": extracted.model_dump(),
            "llm_calls": llm_calls,
            "total_tokens": total_tokens,
            "audit_log": [
                {
                    "stage": "extraction",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "note": f"LLM error: {exc}",
                }
            ],
        }

    # Step 3: Self-consistency check — re-run only on genuinely low confidence.
    # Warnings are informational records (date normalisation, minor guesses) and
    # do NOT indicate the extraction needs to be repeated — triggering a second
    # LLM call on every warning doubles latency for zero accuracy gain.
    if extracted.extraction_confidence < EXTRACTION_CONFIDENCE_THRESHOLD:
        logger.info(
            "Ingestion: low-confidence extraction, running second pass for %s",
            invoice_path,
        )
        try:
            extracted2 = _extract_once(raw_text)
            llm_calls += 1
            extracted2 = _normalise_item_names(extracted2)

            # Compare vendor and amount across two runs
            vendor_mismatch = (
                extracted.vendor
                and extracted2.vendor
                and extracted.vendor.strip().lower()
                != extracted2.vendor.strip().lower()
            )
            amount_mismatch = (
                extracted.amount is not None
                and extracted2.amount is not None
                and abs(extracted.amount - extracted2.amount) > 1.0
            )
            if vendor_mismatch:
                extracted.extraction_warnings.append(
                    f"Self-consistency: vendor disagreed across two extraction runs "
                    f"('{extracted.vendor}' vs '{extracted2.vendor}'). Source document is ambiguous."
                )
                extracted.extraction_confidence = min(
                    extracted.extraction_confidence, 0.5
                )
            if amount_mismatch:
                extracted.extraction_warnings.append(
                    f"Self-consistency: amount disagreed across two extraction runs "
                    f"({extracted.amount} vs {extracted2.amount}). Source document is ambiguous."
                )
                extracted.extraction_confidence = min(
                    extracted.extraction_confidence, 0.5
                )
        except Exception as exc:
            logger.warning("Second-pass extraction failed: %s", exc)

    logger.info(
        "Ingestion complete: vendor=%s, amount=%s, items=%d, confidence=%.2f, warnings=%d",
        extracted.vendor,
        extracted.amount,
        len(extracted.items),
        extracted.extraction_confidence,
        len(extracted.extraction_warnings),
    )

    warnings_count = len(extracted.extraction_warnings)
    note = (
        f"vendor={extracted.vendor}, amount={extracted.amount}, "
        f"items={len(extracted.items)}, confidence={extracted.extraction_confidence:.2f}"
        + (f", {warnings_count} warning(s)" if warnings_count else "")
    )
    return {
        "extracted_data": extracted.model_dump(),
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "audit_log": [
            {
                "stage": "extraction",
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "warning" if warnings_count else "ok",
                "note": note,
            }
        ],
    }
