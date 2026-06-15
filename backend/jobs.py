"""Background job runner: invoice processing off the request thread.

The HTTP submit returns as soon as the invoice row exists (RECEIVED); the actual
work runs here. An invoice's durable status is the source of truth for progress —
clients poll GET /api/invoices/{id} rather than holding a request open across
several LLM calls.

Each run is its own wide event (a job is a peer of a request, not nested inside
one) and its own unit of work, sharing the invoice's trace_id so the enqueuing
request and the work that fulfils it stitch together on one trace.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from . import invoices, pipeline
from .statuses import Status
from .unit_of_work import unit_of_work
from .wide_event import run_job

log = logging.getLogger("jobs")

# Small pool: each job blocks on an LLM call, and concurrent SQLite writers
# contend even under WAL. Bounded concurrency keeps both in check.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="invoice-job")


def submit(invoice_id: int, trace_id: str) -> None:
    _executor.submit(_run, invoice_id, trace_id)


def _run(invoice_id: int, trace_id: str) -> None:
    with run_job("process_invoice", "worker", trace_id) as event:
        pipeline.run(invoice_id, event)
        with unit_of_work(event) as uow:
            status = invoices.get_status(uow, invoice_id)
        if status == Status.FAILED:
            event.escalate("error")
        event.set_business(
            "invoice", {"id": invoice_id, "message": f"invoice {invoice_id} → {status.value}"})
