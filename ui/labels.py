"""Display text, palette constants, and text formatters for InvoiceAI.

All user-visible strings and colour tokens live here so the rest of the UI
never embeds magic labels or hex codes.
"""

from __future__ import annotations

# Maps LangGraph node names to the stepper stage they belong to.
NODE_STEP_MAP: dict[str, str] = {
    "run_ingestion": "Extract",
    "run_validation": "Validate",
    "run_approval": "VP Review",
    "run_critique": "Senior Audit",
    "run_payment": "Payment",
    "run_rejection_log": "Payment",
    # Fallback: bare node names returned by some graph versions
    "ingestion": "Extract",
    "validation": "Validate",
    "approval": "VP Review",
    "critique": "Senior Audit",
    "payment": "Payment",
    "rejection_log": "Payment",
}

PIPELINE_STAGES: list[str] = [
    "Extract",
    "Validate",
    "VP Review",
    "Senior Audit",
    "Payment",
]

ISSUE_TITLES: dict[str, str] = {
    "out_of_stock": "Out of Stock",
    "insufficient_stock": "Insufficient Stock",
    "unknown_item": "Item Not in Catalog",
    "invalid_quantity": "Invalid Quantity",
    "total_mismatch": "Invoice Total Mismatch",
    "price_mismatch": "Unit Price Deviation",
    "revision_detected": "Revised Invoice",
    "revision_of_paid_invoice": "Revision of Paid Invoice",
    "duplicate_invoice": "Duplicate Invoice",
    "foreign_currency": "Non-USD Currency",
}

# Severity → (left-border colour, badge background, badge text colour, glyph)
SEVERITY_THEME: dict[str, tuple[str, str, str, str]] = {
    "error": ("#DC2626", "#FEE2E2", "#DC2626", "✕"),
    "warning": ("#D97706", "#FEF3C7", "#D97706", "⚠"),
    "info": ("#2563EB", "#EFF6FF", "#2563EB", "ℹ"),
}

OUTCOME_LABELS: dict[str, str] = {
    "approved": "PAID",
    "rejected": "REJECTED",
    "needs_review": "NEEDS REVIEW",
}

# Outcome label → (background, text colour) for verdict pills
OUTCOME_COLORS: dict[str, tuple[str, str]] = {
    "PAID": ("#DCFCE7", "#15803D"),
    "REJECTED": ("#FEE2E2", "#DC2626"),
    "NEEDS REVIEW": ("#FEF3C7", "#D97706"),
}

# Audit log status → icon glyph and hex colour
AUDIT_GLYPHS: dict[str, str] = {"ok": "✓", "warning": "⚠", "error": "✗"}
AUDIT_PALETTE: dict[str, str] = {
    "ok": "#16A34A",
    "warning": "#D97706",
    "error": "#DC2626",
}

LLM_PROVIDERS: dict[str, str] = {
    "nvidia": "NVIDIA NIM",
    "grok": "xAI Grok",
}


def issue_label(issue_type: str) -> str:
    """Human-readable title for a validation flag issue type."""
    return ISSUE_TITLES.get(issue_type, issue_type.replace("_", " ").title())


def verdict_label(decision: str | None) -> str:
    """Uppercase display label for an approval decision."""
    return OUTCOME_LABELS.get(decision or "", (decision or "UNKNOWN").upper())
