"""FastAPI app: the single API both the frontend and CLI call."""

import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import invoices, jobs
from .invoices import create_invoice, load_invoice
from .middleware import WideEventMiddleware
from .statuses import Status
from .unit_of_work import unit_of_work
from .wide_event import get_current_event

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
VERIFICATION_RESULTS = Path(__file__).resolve().parent.parent / "evals" / "results.json"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Galatiq Invoice Pipeline")
app.add_middleware(WideEventMiddleware)

api = APIRouter(prefix="/api")


@api.post("/invoices", status_code=202)
def submit_invoice(file: UploadFile = File(...)) -> dict:
    """Accept a document, persist it, and enqueue processing. Returns immediately
    with the RECEIVED invoice; the pipeline (several LLM calls) runs in a
    background job, and the invoice's durable status is what clients poll
    (GET /api/invoices/{id})."""
    suffix = Path(file.filename or "").suffix or ".txt"
    dest = UPLOADS_DIR / f"{uuid4().hex}{suffix}"
    dest.write_bytes(file.file.read())

    event = get_current_event()
    with unit_of_work(event) as uow:
        invoice_id = create_invoice(uow, str(dest), suffix.lstrip("."), trace_id=event.trace_id)
        snapshot = load_invoice(uow, invoice_id)

    jobs.submit(invoice_id, event.trace_id)
    return snapshot


@api.get("/invoices")
def list_invoices(limit: int = 50) -> list[dict]:
    with unit_of_work(get_current_event()) as uow:
        rows = [dict(r) for r in uow.query(
            "SELECT id, status, invoice_number, vendor_raw, currency, stated_total,"
            " review_category, review_level, outcome, source_format, created_at"
            " FROM invoices ORDER BY id DESC LIMIT ?", (limit,))]
        by_invoice: dict = {}
        for c in uow.query("SELECT invoice_id, category, importance FROM review_categories"
                           " ORDER BY importance DESC, id"):
            by_invoice.setdefault(c["invoice_id"], []).append(
                {"category": c["category"], "importance": c["importance"]})
        for r in rows:
            r["categories"] = by_invoice.get(r["id"], [])
        return rows


@api.get("/vendors")
def list_vendors() -> list[dict]:
    with unit_of_work(get_current_event()) as uow:
        return [dict(r) for r in uow.query(
            "SELECT v.id, v.name, v.status, v.currency,"
            " (SELECT COUNT(*) FROM purchase_orders p WHERE p.vendor_id=v.id AND p.status='open')"
            " AS open_pos FROM vendors v ORDER BY v.name")]


@api.get("/purchase-orders")
def list_purchase_orders() -> list[dict]:
    with unit_of_work(get_current_event()) as uow:
        orders = [dict(r) for r in uow.query(
            "SELECT p.id, p.po_number, p.status, p.created_at,"
            " v.id AS vendor_id, v.name AS vendor_name, v.currency,"
            " COALESCE(SUM(l.qty_ordered * l.unit_price), 0) AS total_authorized,"
            " COALESCE(SUM(l.qty_invoiced * l.unit_price), 0) AS total_invoiced,"
            " COUNT(l.id) AS line_count"
            " FROM purchase_orders p"
            " JOIN vendors v ON v.id = p.vendor_id"
            " LEFT JOIN po_lines l ON l.po_id = p.id"
            " GROUP BY p.id"
            " ORDER BY p.status='open' DESC, p.po_number")]
        by_po = {order["id"]: order for order in orders}
        for order in orders:
            order["lines"] = []
        for line in uow.query(
            "SELECT id, po_id, item, qty_ordered, qty_invoiced, unit_price,"
            " (qty_ordered - qty_invoiced) AS qty_remaining"
            " FROM po_lines ORDER BY po_id, id"):
            order = by_po.get(line["po_id"])
            if order is not None:
                order["lines"].append(dict(line))
        return orders


def _ensure_invoice(uow, invoice_id: int) -> None:
    """A client can request any id — a missing one is a clean 404, not an impossible() 500."""
    if not uow.query("SELECT 1 FROM invoices WHERE id=?", (invoice_id,)):
        raise HTTPException(404, f"invoice {invoice_id} not found")


@api.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int) -> dict:
    with unit_of_work(get_current_event()) as uow:
        _ensure_invoice(uow, invoice_id)
        return load_invoice(uow, invoice_id)


_SOURCE_MEDIA = {"pdf": "application/pdf", "json": "application/json", "csv": "text/csv",
                 "xml": "application/xml", "txt": "text/plain"}


@api.get("/invoices/{invoice_id}/source")
def invoice_source(invoice_id: int) -> FileResponse:
    """Stream the original document inline so the reviewer sees exactly what was
    submitted. The path is the one stored on ingest — never client input."""
    with unit_of_work(get_current_event()) as uow:
        rows = uow.query("SELECT source_path, source_format FROM invoices WHERE id=?", (invoice_id,))
    if not rows:
        raise HTTPException(404, f"invoice {invoice_id} not found")
    path = Path(rows[0]["source_path"] or "")
    if not path.is_file():
        raise HTTPException(404, "source document is no longer available")
    media = _SOURCE_MEDIA.get((rows[0]["source_format"] or "").lower(), "text/plain")
    return FileResponse(path, media_type=media, content_disposition_type="inline")


class ApproveBody(BaseModel):
    note: str | None = None


class RejectBody(BaseModel):
    reason: str = Field(min_length=1)


class LineItemEdit(BaseModel):
    id: int
    item_raw: str | None = None
    quantity: float | None = None
    unit_price: float | None = None


class CorrectBody(BaseModel):
    vendor_raw: str | None = None
    invoice_number: str | None = None
    currency: str | None = None
    due_date: str | None = None
    payment_terms: str | None = None
    stated_total: float | None = None
    line_items: list[LineItemEdit] | None = None


def _require_review(uow, invoice_id: int) -> None:
    """A reviewer action is only valid on a held invoice."""
    _ensure_invoice(uow, invoice_id)
    status = invoices.get_status(uow, invoice_id)
    if status != Status.NEEDS_REVIEW:
        raise HTTPException(409, f"invoice {invoice_id} is {status.value}, not awaiting review")


@api.post("/invoices/{invoice_id}/approve")
def approve_invoice(invoice_id: int, body: ApproveBody | None = None) -> dict:
    """Human review resolution: a reviewer clears a held invoice, which pays it.
    The only forward move out of NEEDS_REVIEW — the system never auto-pays what
    it held, but a person can. An optional note is recorded on the trail."""
    with unit_of_work(get_current_event(), immediate=True) as uow:
        _require_review(uow, invoice_id)
        if body and body.note:
            invoices.add_review_note(uow, invoice_id, body.note)
        invoices.set_status(uow, invoice_id, Status.APPROVED)
        invoices.pay(uow, invoice_id, stage="review", trigger="reviewer approved; ")
        return load_invoice(uow, invoice_id)


@api.post("/invoices/{invoice_id}/reject")
def reject_invoice(invoice_id: int, body: RejectBody) -> dict:
    """Human review resolution: a reviewer declines a held invoice. NEEDS_REVIEW ->
    REJECTED, with the reason recorded on the trail. The only path to REJECTED."""
    with unit_of_work(get_current_event(), immediate=True) as uow:
        _require_review(uow, invoice_id)
        invoices.reject(uow, invoice_id, body.reason)
        return load_invoice(uow, invoice_id)


@api.post("/invoices/{invoice_id}/correct")
def correct_invoice(invoice_id: int, body: CorrectBody) -> dict:
    """Fix a field the extractor misread (a wrong amount or vendor) during review.
    Each change is logged old -> new on the trail, then the deterministic checks
    re-run against the corrected data; the judge's verdict is left as-is (it predates
    the edit) and the UI marks it stale. Only valid on a held invoice."""
    fields = body.model_dump(exclude_unset=True, exclude={"line_items"})
    line_edits = [li.model_dump(exclude_unset=True) for li in (body.line_items or [])]
    with unit_of_work(get_current_event(), immediate=True) as uow:
        _require_review(uow, invoice_id)
        if invoices.apply_corrections(uow, invoice_id, fields, line_edits):
            invoices.revalidate(uow, invoice_id)
        return load_invoice(uow, invoice_id)


@api.get("/events")
def list_events(limit: int = 25) -> list[dict]:
    with unit_of_work(get_current_event()) as uow:
        rows = uow.query(
            "SELECT id, trace_id, type, level, source, path, method, status_code,"
            " duration_ms, error, data, created_at FROM wide_events"
            " ORDER BY created_at DESC LIMIT ?", (limit,))
    return [{**dict(r), "error": bool(r["error"]), "data": json.loads(r["data"])} for r in rows]


@api.get("/verification-bench")
def verification_bench() -> dict:
    if not VERIFICATION_RESULTS.exists():
        return {"available": False, "path": str(VERIFICATION_RESULTS)}
    try:
        data = json.loads(VERIFICATION_RESULTS.read_text())
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"verification results are not valid JSON: {exc.msg}") from exc
    return {"available": True, **data}


app.include_router(api)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# Serve the multi-page console (index/review/upload + assets) from the frontend
# directory. Mounted last so the /api/* routes above match first; html=True serves
# index.html at "/" and resolves the relative links the console navigates between.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
