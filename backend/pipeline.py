"""The LangGraph pipeline: document -> persisted, traced, decided invoice.

  ingest ─► extract ─► validate ─┬─► judge ─► gate ─┬─► pay ─► PAID
  (code)    (LLM)     (code)      │   (LLM)   (code) │
                                  │                  └─► NEEDS_REVIEW
                                  └─► supersede ─► SUPERSEDED   (exact duplicate)

The decision is a two-layer AND: `validate` (deterministic) decides what is
verifiably wrong; `judge` (LLM) reads the document plus those findings and adds
qualitative judgment and a human-readable category. The `gate` pays touchless
only when the hard checks don't block AND the judge recommends it — code makes
the move, the judge's verdict is only an input it reads.
"""

import logging
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from . import agents, invoices, payments, tracing, validation
from .impossible import impossible
from .ingestion import SourceDoc, load_document
from .llm import RunContext
from .schemas import ExtractedInvoice, JudgeVerdict
from .statuses import TERMINAL, Status
from .unit_of_work import UnitOfWork, unit_of_work
from .validation import ValidationResult

log = logging.getLogger("pipeline")


class PipelineState(TypedDict, total=False):
    source_path: str
    doc: SourceDoc
    extracted: ExtractedInvoice
    validation: ValidationResult
    verdict: JudgeVerdict
    decision: str  # "pay" | "hold", set by the gate to route to the pay node


def build_graph(ctx: RunContext):
    """Nodes are closures over ctx (invoice id + wide event) and receive their own
    unit of work as a parameter — the per-stage transaction boundary. Nodes that
    call an LLM do so before they write, and the LLM layer traces each exchange in
    its own transaction, so no write transaction is ever held across an LLM call."""

    def ingest(state: PipelineState, uow: UnitOfWork) -> PipelineState:
        doc = load_document(Path(state["source_path"]))
        invoices.set_status(uow, ctx.invoice_id, Status.PROCESSING)
        tracing.emit(uow, ctx.invoice_id, "ingest", "route",
                     {"kind": doc.kind, "summary": doc.route_note})
        return {"doc": doc}

    def extract(state: PipelineState, uow: UnitOfWork) -> PipelineState:
        ex = agents.extract(ctx, state["doc"])
        invoices.save_extraction(uow, ctx.invoice_id, ex)
        return {"extracted": ex}

    def validate(state: PipelineState, uow: UnitOfWork) -> PipelineState:
        result = validation.validate(uow, ctx.invoice_id)
        invoices.save_validation(uow, ctx.invoice_id, result)
        tracing.emit(uow, ctx.invoice_id, "validate", "check",
                     {"summary": f"{len(result.findings)} finding(s), "
                                 f"{'blocking' if result.blocking else 'clean'}",
                      "findings": [f.code.value for f in result.findings],
                      "blocking": result.blocking})
        return {"validation": result}

    def judge(state: PipelineState, uow: UnitOfWork) -> PipelineState:
        result = state["validation"]
        findings = [{"code": f.code.value, "severity": f.severity.value, "message": f.message}
                    for f in result.findings]
        verdict = agents.judge(ctx, state["doc"], state["extracted"], findings,
                               [c.value for c in result.suggested_categories], result.blocking)
        invoices.save_verdict(uow, ctx.invoice_id, verdict)
        tracing.emit(uow, ctx.invoice_id, "judge", "verdict",
                     {"summary": verdict.summary,
                      "recommendation": "pay" if verdict.pay else "hold",
                      "category": verdict.review_category.value if verdict.review_category else None,
                      "level": verdict.level.value})
        return {"verdict": verdict}

    def supersede(state: PipelineState, uow: UnitOfWork) -> PipelineState:
        dup = state["validation"].duplicate_of
        invoices.set_status(uow, ctx.invoice_id, Status.SUPERSEDED)
        invoices.set_outcome(uow, ctx.invoice_id, "superseded", superseded_by=dup)
        tracing.emit(uow, ctx.invoice_id, "finalize", "gate",
                     {"outcome": "superseded", "summary": f"exact duplicate of invoice #{dup}"})
        return {}

    def gate(state: PipelineState, uow: UnitOfWork) -> PipelineState:
        result, verdict = state["validation"], state["verdict"]
        if not result.blocking and verdict.pay:
            invoices.set_review_category(uow, ctx.invoice_id, None)  # paid: no hold reason
            invoices.set_status(uow, ctx.invoice_id, Status.APPROVED)
            invoices.set_outcome(uow, ctx.invoice_id, "approved")
            tracing.emit(uow, ctx.invoice_id, "finalize", "gate",
                         {"outcome": "approved",
                          "summary": "hard checks passed and the judge cleared it — paying"})
            return {"decision": "pay"}

        category = verdict.review_category or (
            result.suggested_categories[0] if result.suggested_categories else None)
        if verdict.review_category is None and category is not None:
            invoices.set_review_category(uow, ctx.invoice_id, category)
        invoices.set_status(uow, ctx.invoice_id, Status.NEEDS_REVIEW)
        invoices.set_outcome(uow, ctx.invoice_id, "needs_review")
        tracing.emit(uow, ctx.invoice_id, "finalize", "gate",
                     {"outcome": "needs_review", "summary": verdict.summary,
                      "category": category.value if category is not None else None,
                      "blocking": result.blocking, "judge_recommended_pay": verdict.pay})
        return {"decision": "hold"}

    def pay(state: PipelineState, uow: UnitOfWork) -> PipelineState:
        ex = state["extracted"]
        receipt = payments.pay(ex.vendor_name or "(unknown)", ex.stated_total or 0.0, ex.currency)
        drawdown = invoices.apply_po_drawdown(uow, ctx.invoice_id)
        invoices.set_status(uow, ctx.invoice_id, Status.PAID)
        invoices.set_outcome(uow, ctx.invoice_id, "paid")
        tracing.emit(uow, ctx.invoice_id, "pay", "payment",
                     {"summary": f"paid {receipt['amount']} {receipt['currency']} to {receipt['vendor']}",
                      "reference": receipt["reference"], "drawdown": drawdown})
        return {}

    def transactional(fn):
        """Wrap a node so it runs in its own short unit of work, passed in and
        committed when the node returns. This is the per-stage transaction
        boundary: each stage lands atomically and visibly (a poller sees progress),
        and no transaction spans more than one stage."""
        def wrapped(state: PipelineState) -> PipelineState:
            with unit_of_work(ctx.event) as uow:
                return fn(state, uow)
        return wrapped

    sg = StateGraph(PipelineState)
    for name, fn in [("ingest", ingest), ("extract", extract), ("validate", validate),
                     ("judge", judge), ("supersede", supersede), ("gate", gate), ("pay", pay)]:
        sg.add_node(name, transactional(fn))

    sg.add_edge(START, "ingest")
    sg.add_edge("ingest", "extract")
    sg.add_edge("extract", "validate")
    sg.add_conditional_edges(
        "validate", lambda s: "supersede" if s["validation"].duplicate_of else "judge",
        {"supersede": "supersede", "judge": "judge"})
    sg.add_edge("judge", "gate")
    sg.add_conditional_edges(
        "gate", lambda s: "pay" if s.get("decision") == "pay" else "done",
        {"pay": "pay", "done": END})
    sg.add_edge("supersede", END)
    sg.add_edge("pay", END)
    return sg.compile()


def run(invoice_id: int, event=None) -> dict:
    """Run the graph for an already-created (RECEIVED) invoice, driving it to a
    durable resting status, and return the full invoice. Each node commits its
    own short transaction (see `transactional`), bound to `event` so DB work is
    billed to the job's wide event.

    On failure the invoice lands in FAILED — logged, recorded on the trace, never
    stranded mid-pipeline. The failure is not re-raised: the per-node commits have
    already persisted the work up to the break, and FAILED is recorded here in its
    own transaction."""
    with unit_of_work(event) as uow:
        rows = uow.query("SELECT source_path FROM invoices WHERE id=?", (invoice_id,))
        if not rows:
            impossible("pipeline run for a missing invoice", {"invoice_id": invoice_id})
        source_path = rows[0]["source_path"]

    ctx = RunContext(invoice_id=invoice_id, event=event)
    try:
        build_graph(ctx).invoke({"source_path": source_path})
    except Exception as exc:
        log.exception("pipeline failed for invoice %s", invoice_id)
        with unit_of_work(event) as uow:
            if invoices.get_status(uow, invoice_id) not in TERMINAL:
                invoices.set_status(uow, invoice_id, Status.FAILED)
            tracing.emit(uow, invoice_id, "pipeline", "error",
                         {"summary": f"processing failed: {type(exc).__name__}", "error": str(exc)})

    with unit_of_work(event) as uow:
        return invoices.load_invoice(uow, invoice_id)
