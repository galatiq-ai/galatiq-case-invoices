"""Shared tools used by agents — mock payment, file reading, observability."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .database import query_inventory, is_invoice_already_processed

TRACES_DIR = Path("traces")


def mock_payment(vendor: str, amount: float) -> dict:
    """Simulate a payment API call. Returns a status dict."""
    tx_id = f"TX-{uuid.uuid4().hex[:8].upper()}"
    print(f"  [PAYMENT] Paid ${amount:,.2f} to '{vendor}' — TX {tx_id}")
    return {"status": "success", "tx_id": tx_id}


def read_file_content(file_path: str) -> str:
    """Read raw text content from a file path, handling various formats."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Invoice file not found: {file_path}")

    ext = path.suffix.lower()

    if ext == ".json":
        return _read_json(path)
    elif ext == ".csv":
        return _read_csv(path)
    elif ext == ".xml":
        return _read_xml(path)
    elif ext == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        return _read_pdf(path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _read_json(path: Path) -> str:
    """Read a JSON file and return it as formatted text for LLM extraction."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return json.dumps(data, indent=2)


def _read_csv(path: Path) -> str:
    """Read a CSV file and return as text."""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_xml(path: Path) -> str:
    """Read an XML file and return as text."""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF file using text extraction, then OCR fallback."""
    def _extract_with_fitz() -> str:
        try:
            import fitz

            doc = fitz.open(str(path))
            try:
                text_parts = [page.get_text("text", sort=True) for page in doc]
                text = "".join(text_parts)
                if text.strip():
                    return text

                try:
                    from PIL import Image
                    import pytesseract
                except Exception:
                    return ""

                ocr_parts: list[str] = []
                matrix = fitz.Matrix(2, 2)
                for page in doc:
                    pix = page.get_pixmap(matrix=matrix, alpha=False)
                    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    page_text = pytesseract.image_to_string(image)
                    if page_text:
                        ocr_parts.append(page_text)
                return "\n".join(ocr_parts)
            finally:
                doc.close()
        except Exception:
            return ""

    def _extract_with_pdfplumber() -> str:
        try:
            import pdfplumber

            text_parts: list[str] = []
            with pdfplumber.open(str(path)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
            return "\n".join(text_parts)
        except ImportError:
            raise RuntimeError(
                "No PDF library available. Install PyMuPDF or pdfplumber."
            )

    text = _extract_with_fitz()
    if not text.strip():
        text = _extract_with_pdfplumber()

    if not text.strip():
        raise ValueError(
            f"No text could be extracted from PDF: {path.name}. "
            "If this is a scanned document, install optional OCR dependencies."
        )

    return text


def write_trace(state: dict, agent_name: str) -> None:
    """Write a trace entry for an agent step."""
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    inv_num = "unknown"
    if state.get("extracted_invoice") and state["extracted_invoice"].get("invoice_number"):
        inv_num = state["extracted_invoice"]["invoice_number"]
    elif state.get("file_path"):
        inv_num = Path(state["file_path"]).stem

    trace_file = TRACES_DIR / f"{inv_num}.json"
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent_name,
        "state_snapshot": {
            k: v for k, v in state.items()
            if k != "raw_text" and k != "trace_log"
        },
    }

    existing = []
    if trace_file.exists():
        try:
            existing = json.loads(trace_file.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = [existing]
        except (json.JSONDecodeError, Exception):
            existing = []

    existing.append(entry)
    trace_file.write_text(
        json.dumps(existing, indent=2, default=str), encoding="utf-8"
    )


def get_error_context(error: Exception) -> str:
    """Extract a user-friendly error message from an exception."""
    return f"{type(error).__name__}: {str(error)}"
