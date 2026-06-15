"""FastAPI app: the single API both the frontend and CLI call."""

import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import invoices, jobs, payments, tracing
from .invoices import create_invoice, load_invoice
from .middleware import WideEventMiddleware
from .statuses import Status
from .unit_of_work import unit_of_work
from .wide_event import get_current_event

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"
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
        return [dict(r) for r in uow.query(
            "SELECT id, status, invoice_number, vendor_raw, currency, stated_total,"
            " source_format, created_at FROM invoices ORDER BY id DESC LIMIT ?", (limit,))]


@api.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int) -> dict:
    with unit_of_work(get_current_event()) as uow:
        return load_invoice(uow, invoice_id)


@api.post("/invoices/{invoice_id}/approve")
def approve_invoice(invoice_id: int) -> dict:
    """Human review resolution: a reviewer clears a held invoice, which pays it.
    The only forward move out of NEEDS_REVIEW — the system never auto-pays what
    it held, but a person can."""
    with unit_of_work(get_current_event()) as uow:
        status = invoices.get_status(uow, invoice_id)
        if status != Status.NEEDS_REVIEW:
            raise HTTPException(409, f"invoice {invoice_id} is {status.value}, not awaiting review")
        inv = uow.query(
            "SELECT vendor_raw, stated_total, currency FROM invoices WHERE id=?", (invoice_id,))[0]
        invoices.set_status(uow, invoice_id, Status.APPROVED)
        receipt = payments.pay(inv["vendor_raw"] or "(unknown)", inv["stated_total"] or 0.0, inv["currency"])
        drawdown = invoices.apply_po_drawdown(uow, invoice_id)
        invoices.set_status(uow, invoice_id, Status.PAID)
        invoices.set_outcome(uow, invoice_id, "paid")
        tracing.emit(uow, invoice_id, "review", "human_approve",
                     {"summary": f"reviewer approved; paid {receipt['amount']} {receipt['currency']}"
                                 f" to {inv['vendor_raw']}", "reference": receipt["reference"],
                      "drawdown": drawdown})
        return load_invoice(uow, invoice_id)


@api.get("/events")
def list_events(limit: int = 25) -> list[dict]:
    with unit_of_work(get_current_event()) as uow:
        rows = uow.query(
            "SELECT id, trace_id, type, level, source, path, method, status_code,"
            " duration_ms, error, data, created_at FROM wide_events"
            " ORDER BY created_at DESC LIMIT ?", (limit,))
    return [{**dict(r), "error": bool(r["error"]), "data": json.loads(r["data"])} for r in rows]


app.include_router(api)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
