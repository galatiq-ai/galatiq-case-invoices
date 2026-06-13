"""Shared state and schema definitions for the invoice processing pipeline."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional, TypedDict

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    """A single line item on an invoice."""
    item: str
    qty: int
    unit_price: Optional[float] = None
    amount: Optional[float] = None
    note: Optional[str] = None


class ExtractedInvoice(BaseModel):
    """Structured data extracted from an invoice document."""
    invoice_number: str
    vendor: str
    date: Optional[str] = None
    due_date: Optional[str] = None
    items: list[LineItem] = Field(default_factory=list)
    total: float = 0.0
    currency: str = "USD"
    raw_text: str = ""
    extraction_method: str = "llm"  # "llm" or "deterministic"
    confidence: float = 1.0
    ingestion_errors: list[str] = Field(default_factory=list)


class InventoryCheck(BaseModel):
    """Result of a single inventory check on one item."""
    item: str
    requested_qty: int
    available_stock: Optional[int] = None
    status: str = ""  # "ok", "stock_mismatch", "unknown_item", "out_of_stock"
    message: str = ""


class IntegrityCheck(BaseModel):
    """Result of a data integrity check."""
    field: str
    issue: str
    severity: str = "error"  # "error" or "warning"


class ValidationResult(BaseModel):
    """Aggregated validation results for an invoice."""
    invoice_number: str
    inventory_checks: list[InventoryCheck] = Field(default_factory=list)
    integrity_errors: list[IntegrityCheck] = Field(default_factory=list)
    passed: bool = True
    summary: str = ""


class ApprovalDecision(BaseModel):
    """Decision made by the approval agent."""
    decision: str = "pending"  # "approved", "rejected", "hold", "pending"
    reason: str = ""
    required_actions: list[str] = Field(default_factory=list)
    correction_count: int = 0
    correction_hints: list[str] = Field(default_factory=list)


class PaymentResult(BaseModel):
    """Result of a payment attempt."""
    status: str = ""  # "success", "failed", "skipped"
    tx_id: Optional[str] = None
    vendor: str = ""
    amount: float = 0.0
    error: Optional[str] = None


class AuditEvent(BaseModel):
    """A structured audit/event record for an invoice stage."""
    timestamp: Optional[datetime] = None
    invoice_number: Optional[str] = None
    file_name: Optional[str] = None
    stage: Optional[str] = None
    status: Optional[str] = None
    decision: Optional[str] = None
    reason: Optional[str] = None
    flags: list[str] = Field(default_factory=list)
    actor: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    total: Optional[float] = None
    vendor: Optional[str] = None


class InvoiceSummary(BaseModel):
    invoice_number: str
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    status: Optional[str] = None
    total: Optional[float] = None
    vendor: Optional[str] = None
    events_count: int = 0


class InvoiceState(TypedDict, total=False):
    """Complete state that flows through the LangGraph pipeline."""
    file_path: str
    raw_text: str
    extracted_invoice: Optional[dict]
    validation_result: Optional[dict]
    approval_decision: Optional[dict]
    payment_result: Optional[dict]
    error: Optional[str]
    trace_log: list[dict]
