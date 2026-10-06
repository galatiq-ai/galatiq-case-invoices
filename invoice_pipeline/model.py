import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from pydantic import BaseModel, BeforeValidator


class Severity(StrEnum):
    REJECTION_RULE = "rejection_rule"
    REVIEW_TRIGGER = "review_trigger"
    WARNING = "warning"


class FindingCode(StrEnum):
    """Stable Finding codes. Values equal names (e.g. "VENDOR_BLOCKED")."""

    # Rejection Rules
    VENDOR_BLOCKED = "VENDOR_BLOCKED"
    ITEM_ZERO_STOCK = "ITEM_ZERO_STOCK"
    ITEM_UNKNOWN = "ITEM_UNKNOWN"
    QUANTITY_INVALID = "QUANTITY_INVALID"
    INCOMPLETE_IDENTITY = "INCOMPLETE_IDENTITY"
    # Review Triggers
    STOCK_SHORTAGE = "STOCK_SHORTAGE"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    NONPOSITIVE_TOTAL = "NONPOSITIVE_TOTAL"
    VENDOR_LOOKALIKE = "VENDOR_LOOKALIKE"
    CURRENCY_NO_RATE = "CURRENCY_NO_RATE"
    PARTIAL_IDENTITY = "PARTIAL_IDENTITY"
    REVISION_PAYMENT_DELTA = "REVISION_PAYMENT_DELTA"
    UNREADABLE_DOCUMENT = "UNREADABLE_DOCUMENT"
    LLM_EXTRACTED = "LLM_EXTRACTED"
    # Warnings
    VENDOR_UNKNOWN = "VENDOR_UNKNOWN"
    PRICE_DEVIATION = "PRICE_DEVIATION"
    CURRENCY_NON_USD = "CURRENCY_NON_USD"


# Duplicate Payment is an arrival classification (Decision Duplicate), not a Finding.

_R, _T, _W = Severity.REJECTION_RULE, Severity.REVIEW_TRIGGER, Severity.WARNING
SEVERITY: dict[FindingCode, Severity] = {
    FindingCode.VENDOR_BLOCKED: _R,
    FindingCode.ITEM_ZERO_STOCK: _R,
    FindingCode.ITEM_UNKNOWN: _R,
    FindingCode.QUANTITY_INVALID: _R,
    FindingCode.INCOMPLETE_IDENTITY: _R,
    FindingCode.STOCK_SHORTAGE: _T,
    FindingCode.RECONCILIATION_MISMATCH: _T,
    FindingCode.MISSING_REQUIRED_FIELD: _T,
    FindingCode.NONPOSITIVE_TOTAL: _T,
    FindingCode.VENDOR_LOOKALIKE: _T,
    FindingCode.CURRENCY_NO_RATE: _T,
    FindingCode.PARTIAL_IDENTITY: _T,
    FindingCode.REVISION_PAYMENT_DELTA: _T,
    FindingCode.UNREADABLE_DOCUMENT: _T,
    FindingCode.LLM_EXTRACTED: _T,
    FindingCode.VENDOR_UNKNOWN: _W,
    FindingCode.PRICE_DEVIATION: _W,
    FindingCode.CURRENCY_NON_USD: _W,
}


class Finding(BaseModel, frozen=True):
    code: FindingCode
    severity: Severity
    detail: str
    line: int | None = None  # line-item index when item-scoped


def finding(code: FindingCode, detail: str, line: int | None = None) -> Finding:
    """The only Finding constructor: severity always comes from `SEVERITY`."""
    return Finding(code=code, severity=SEVERITY[code], detail=detail, line=line)


def _no_float(value):
    if isinstance(value, float):
        raise ValueError("money and quantities must be Decimal or str, never float")
    return value


Money = Annotated[Decimal, BeforeValidator(_no_float)]


def vendor_key(name: str | None) -> str | None:
    """Comparison key: trimmed, case-folded, whitespace-collapsed. None when blank."""
    key = " ".join((name or "").split()).casefold()
    return key or None


def normalize_invoice_number(raw: str | None) -> str | None:
    """Canonical `INV-<digits>` when digits are present; otherwise the upper-cased text."""
    text = (raw or "").strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return f"INV-{digits}" if digits else text.upper()


class LineItem(BaseModel):
    raw_name: str
    sku: str | None  # normalized; None when not recognizable
    raw_quantity: str | None  # original token, retained even when non-numeric
    quantity: Money | None  # None/zero/negative/fractional -> QUANTITY_INVALID
    unit_price: Money | None
    line_total: Money | None
    note: str | None = None  # context, never an exemption


class Invoice(BaseModel):
    invoice_number: str | None
    vendor: str | None
    revision: str | None = None
    invoice_date: date | None
    due_date_text: str | None  # raw context only (no deadline is derived)
    payment_terms: str | None  # raw context only
    currency: str  # ISO-4217 upper-case
    items: list[LineItem]
    subtotal: Money | None
    tax: Money | None
    shipping: Money | None
    total: Money | None
    notes: str | None
    po_reference: str | None
    source_path: str
    source_format: Literal["txt", "json", "csv", "xml", "pdf"]
    extracted_fields: list[str] = []  # fields supplied by the Extraction Fallback

    def identity(self) -> tuple[str, str] | None:
        """(vendor_key, normalized number); None unless both parts are present."""
        key, number = vendor_key(self.vendor), normalize_invoice_number(self.invoice_number)
        return (key, number) if key and number else None


class Repair(BaseModel):
    field: str  # "invoice_date", "items[2].line_total"
    raw: str  # "2O26"
    repaired: str  # "2026"


class Ingested(BaseModel):
    invoice: Invoice | None  # None -> Unreadable Document
    findings: list[Finding]
    repairs: list[Repair] = []  # Reviewer evidence only; never in the Case File
    unreadable_reason: str | None = None  # "<step>: <ErrorType>: <message>" when invoice is None
    raw_text: str | None = None  # TXT / PDF text layer; input of the Extraction Fallback
    missing_required: list[Literal["vendor", "invoice_number", "total", "items"]] = []


class UsdEquivalent(BaseModel):
    amount: Decimal
    rate: Decimal
    as_of: date
    buffer: Decimal


class References(BaseModel):
    """What each Warning was measured against (the Case File states it; no model arithmetic)."""

    reference_prices: dict[str, Decimal]  # sku -> catalog unit price, invoiced SKUs only
    stock_levels: dict[str, Decimal]  # sku -> Stock Level, invoiced SKUs only
    aggregated_quantities: dict[str, Decimal]  # valid quantities per invoiced SKU
    price_tolerance: Decimal
    price_deviations: dict[int, Decimal]  # line index -> |unit - ref| / ref (0.20 = 20%)
    usd_equivalent: UsdEquivalent | None  # empty until Reference Rates exist (slice 3)
    heightened_scrutiny_line: Decimal


class HistoryEntry(BaseModel, frozen=True):
    """One prior Ledger arrival of a vendor, as the Critic sees it."""

    number: str
    total: Decimal | None
    currency: str
    state: str  # arrivals.state, e.g. "paid", "logged_rejection"
    date: dt.date | None  # invoice date of that arrival


class ArrivalSummary(BaseModel):
    """`ledger.classify()` result as the Case File carries it."""

    kind: Literal["new", "duplicate", "revision", "supersedes"]
    duplicate_of: int | None = None
    paid_to_date: Decimal | None = None  # claimed amount for duplicate/revision
    claimed_state: Literal["paid", "payment_pending"] = "paid"  # state of the claimed arrival
    amount_due: Decimal | None = None


class CaseFile(BaseModel):
    invoice: Invoice
    findings: list[Finding]
    arrival: ArrivalSummary
    decision_context: list[str] = []  # precedence row + reasons; advisory reasoning only
    checklist: dict[FindingCode, tuple[str, ...]] = {}  # only the Warnings present
    references: References
    vendor_history: list[HistoryEntry]  # newest first, at most VENDOR_HISTORY_LIMIT
    vendor_history_total: int  # all prior arrivals of this vendor (may exceed the list)


class LLMExchange(BaseModel):
    """One chat-completions request."""

    raw_answer: str | None  # None when nothing arrived
    error: str | None  # "offline tier", timeout, LLMError, schema error
    called_at: dt.datetime
    elapsed_ms: int | None


class Try(BaseModel):
    """One Correction Wrapper try."""

    exchanges: list[LLMExchange]
    correction: str | None = None  # corrective message sent after this try failed


class RoleCall(BaseModel):
    """Audit record of a single-call role (escalate-only, advisory, extraction)."""

    role: Literal["escalate_review", "advisory", "extraction"]
    tier: str
    model: str | None
    tries: list[Try]
    answer: dict[str, Any] | None  # parsed answer, or None (error says why)
    error: str | None = None


class Outcome(StrEnum):
    APPROVED = "approved"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


class Decision(BaseModel):
    outcome: Outcome
    reasons: list[str]  # codes / rule names / failing Warnings that drove it
    precedence_row: int  # the six rows of the Rule Engine
    decided_by: Literal["rule_engine", "llm_critic"]
    duplicate_of: int | None = None  # row 1 only
    escalate_review: RoleCall | None = None  # row 6
    advisory: RoleCall | None = None  # rows 2-5 without the full gate; never read by decide
    bound_failures: list[str] = []  # Critic Bound failures (row 5); no full gate when non-empty
    unreviewed_warnings: bool = False


@dataclass(frozen=True)
class Agents:
    """Model roles injected into `approval.decide`; scripted fakes in tests.

    Slice 1 supplies offline callables.
    """

    escalate_review: Callable[[CaseFile], RoleCall]
    advise: Callable[[CaseFile, Decision], RoleCall]


class PaymentIssue(BaseModel):
    what: str
    when: dt.datetime
    bank_response: dict[str, Any] | None = None


class QueueItem(BaseModel):
    """One Review Queue row as presenters see it."""

    arrival_id: int
    vendor: str | None
    invoice_number: str | None
    source: str
    total: Decimal | None
    currency: str | None
    state: str
    reasons: list[str]
    payment_issue: PaymentIssue | None = None


@dataclass(frozen=True)
class Event:
    """Pipeline event delivered through `Runtime.on_event`."""

    name: str
    file: str
    detail: dict[str, Any] = field(default_factory=dict)
