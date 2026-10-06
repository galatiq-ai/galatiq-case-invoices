"""Deterministic Ingestion: one document in, a list of typed invoices out (one per invoice;
an Unreadable Document is a one-element list)."""

from pathlib import Path

from invoice_pipeline.ingestion.pdf import parse_pdf
from invoice_pipeline.ingestion.structured import parse_csv, parse_json, parse_xml
from invoice_pipeline.ingestion.text import parse_text
from invoice_pipeline.model import FindingCode, Ingested, finding


def _unreadable(reason: str, raw_text: str | None = None) -> Ingested:
    return Ingested(
        invoice=None,
        findings=[finding(FindingCode.UNREADABLE_DOCUMENT, reason)],
        unreadable_reason=reason,
        raw_text=raw_text,
    )


def ingest(path: Path | str) -> list[Ingested]:
    """Never raises: any failure becomes an Unreadable Document naming step, type and message."""
    path = Path(path)
    step = "route"
    try:
        suffix = path.suffix.lower()
        if suffix not in (".txt", ".json", ".csv", ".xml", ".pdf"):
            return [_unreadable(f"route: unsupported file type {path.suffix!r}")]
        step = "read"
        if not path.read_bytes().strip():
            return [_unreadable("read: the file is empty")]
        step = "parse"
        if suffix == ".txt":
            results = [parse_text(path.read_text(encoding="utf-8"), path.name, "txt")]
        elif suffix == ".json":
            results = [parse_json(path.read_text(encoding="utf-8"), path.name)]
        elif suffix == ".csv":
            results = parse_csv(path.read_text(encoding="utf-8"), path.name)
        elif suffix == ".xml":
            results = [parse_xml(path.read_text(encoding="utf-8"), path.name)]
        else:
            results = [parse_pdf(path)]
        return [
            _unreadable("parse: no line item could be recovered", r.raw_text)
            if r.invoice is not None and not r.invoice.items
            else r
            for r in results
        ]
    except Exception as exc:
        return [_unreadable(f"{step}: {type(exc).__name__}: {exc}")]
