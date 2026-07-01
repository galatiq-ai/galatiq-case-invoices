"""Shared state and sub-schemas for the invoice pipeline."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, Optional
from typing_extensions import TypedDict
from pydantic import BaseModel, Field


# ── Sub-schemas (Pydantic, used for LLM structured outputs) ──────────────────


class LineItem(BaseModel):
    name: str = Field(description="Normalised item name as it appears in inventory")
    qty: Optional[int] = Field(
        default=None,
        description="Requested quantity (must be a positive integer); null if missing, negative, or non-numeric",
    )
    unit_price: Optional[float] = Field(
        default=None, description="Unit price stated on the invoice in invoice currency"
    )


class ExtractedData(BaseModel):
    invoice_number: Optional[str] = Field(default=None)
    vendor: Optional[str] = Field(default=None)
    amount: Optional[float] = Field(
        default=None, description="Grand total as stated on the invoice"
    )
    currency: Optional[str] = Field(
        default="USD", description="ISO 4217 currency code, e.g. USD or EUR"
    )
    items: list[LineItem] = Field(default_factory=list)
    due_date: Optional[str] = Field(
        default=None, description="Due date in ISO 8601 format YYYY-MM-DD if parseable"
    )
    subtotal: Optional[float] = Field(default=None)
    tax_amount: Optional[float] = Field(default=None)
    # Confidence 0–1: how certain the LLM is about the extracted fields.
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    extraction_warnings: list[str] = Field(default_factory=list)


# Severity tier drives both the UI colour and pre-LLM routing weight:
#   error   - hard-stop issues (out_of_stock, unknown_item, duplicate_invoice)
#   warning - significant but recoverable (price/stock/total deviations)
#   info    - advisory only (foreign_currency, revision_detected)
_SEVERITY: dict[str, Literal["info", "warning", "error"]] = {
    "out_of_stock": "error",
    "unknown_item": "error",
    "duplicate_invoice": "error",
    "insufficient_stock": "error",
    "price_mismatch": "warning",
    "total_mismatch": "warning",
    "invalid_quantity": "warning",
    "foreign_currency": "info",
    "revision_detected": "info",
    "revision_of_paid_invoice": "warning",
}


class ValidationFlag(BaseModel):
    item: str
    issue_type: Literal[
        "out_of_stock",
        "insufficient_stock",
        "unknown_item",
        "invalid_quantity",
        "total_mismatch",
        "price_mismatch",
        "revision_detected",
        "revision_of_paid_invoice",
        "duplicate_invoice",
        "foreign_currency",
    ]
    detail: str
    severity: Literal["info", "warning", "error"] = "warning"

    def model_post_init(self, __context: Any) -> None:
        # Always derive severity from the canonical issue_type table.
        # Caller-supplied values are intentionally overridden — all flags
        # created by the validation agent go through this model.
        if self.issue_type in _SEVERITY:
            object.__setattr__(self, "severity", _SEVERITY[self.issue_type])


class ApprovalDecision(BaseModel):
    decision: Literal["approved", "rejected"]
    reasoning: str = Field(description="Full chain-of-thought reasoning")
    key_factors: list[str] = Field(
        default_factory=list,
        description="Bullet-point summary of the 2-4 most important factors",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class CritiqueOutput(BaseModel):
    critique: str = Field(
        description=(
            "Detailed critique of the VP's reasoning. "
            "Never outputs approve/reject — only reasoning review."
        )
    )
    concerns: list[str] = Field(
        default_factory=list,
        description="Specific unresolved concerns or logical gaps found",
    )


# ── Main state TypedDict ──────────────────────────────────────────────────────


class InvoiceState(TypedDict, total=False):
    # Input
    invoice_path: str

    # Ingestion output
    extracted_data: dict[str, Any]
    llm_calls: int
    total_tokens: int

    # Validation output
    validation_flags: list[dict]
    is_duplicate: bool

    # Approval — first pass
    approval_reasoning: str
    review_count: int

    # Critique — set when approval escalates: high-value, error flags,
    # low VP confidence, rejection (second opinion), or approval with active flags.
    critique_notes: str

    # Final decision
    final_decision: Literal["approved", "rejected", "needs_review"] | None
    decision_reasoning: str

    # Revision delta: set when a revision of an already-paid invoice is detected.
    # Payment node uses this instead of the full invoice amount.
    revision_delta_amount: float | None

    # Payment
    payment_result: dict[str, Any] | None

    # Immutable audit trail — each node appends one entry; LangGraph merges
    # with operator.add so entries accumulate rather than overwrite.
    audit_log: Annotated[list[dict], operator.add]

    # Run metadata
    run_id: str
    error: str | None
