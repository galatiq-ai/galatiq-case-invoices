"""Centralized audit helper that normalizes pipeline audit events.

This keeps calls to `database.write_audit_event` in one place and ensures
writes are best-effort and instrumented with metrics/logging.
"""
from __future__ import annotations

import json
from typing import Optional

from .database import write_audit_event
from .observability import get_logger, audit_events_total


def record_stage_event(
    invoice_number: str,
    file_name: str,
    stage: str,
    status: str,
    decision: str = "",
    reason: str = "",
    flags: str = "",
    actor: str = "",
    metadata: Optional[dict] = None,
    total: float = 0.0,
    vendor: str = "",
) -> None:
    """Write one audit event without raising on failure.

    `metadata` will be JSON-serialized when provided.
    """
    logger = get_logger(__name__)
    try:
        meta_text = json.dumps(metadata) if metadata is not None else None
        write_audit_event(
            invoice_number=str(invoice_number or ""),
            stage=stage,
            status=status,
            decision=decision or "",
            reason=reason or "",
            flags=flags or "",
            actor=actor or "",
            metadata=meta_text,
            file_name=file_name or "",
            total=total or 0.0,
            vendor=vendor or "",
        )
        try:
            if audit_events_total is not None:
                audit_events_total.inc()
        except Exception:
            # Non-fatal: metrics increment failure should not break pipeline
            logger.exception("failed to increment audit_events_total metric")
    except Exception:
        logger.exception(
            "failed to write audit event: %s %s %s",
            invoice_number,
            stage,
            status,
        )


def record_pipeline_summary(result: dict) -> None:
    """Write a final pipeline summary event for an invoice run."""
    try:
        invoice = result.get("extracted_invoice") or {}
        invoice_number = invoice.get("invoice_number") if isinstance(invoice, dict) else None
        file_name = result.get("file_path", "")
        status = "failed" if result.get("error") else "success"
        decision = None
        vendor = None
        total = 0.0
        pr = result.get("payment_result") or {}
        if isinstance(invoice, dict):
            vendor = invoice.get("vendor")
            total = float(invoice.get("total", 0) or 0)
        if isinstance(pr, dict) and pr.get("status"):
            decision = pr.get("status")

        record_stage_event(
            invoice_number=invoice_number or "",
            file_name=file_name or "",
            stage="pipeline_summary",
            status=status,
            decision=decision or "",
            reason=(result.get("error") or "")[:1000],
            actor="pipeline",
            metadata={"trace": result.get("trace_log", [])},
            total=total or 0.0,
            vendor=vendor or "",
        )
    except Exception:
        get_logger(__name__).exception("failed to record pipeline summary")


def record_pipeline_error(invoice_number: str, file_name: str, stage: str, error: str) -> None:
    try:
        record_stage_event(
            invoice_number=invoice_number or "",
            file_name=file_name or "",
            stage=stage,
            status="failed",
            reason=str(error),
            actor="pipeline",
        )
    except Exception:
        get_logger(__name__).exception("failed to record pipeline error")
