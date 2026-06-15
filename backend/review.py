"""The review vocabulary: why an invoice can't pay touchless, and how loud that is.

Two independent axes, deliberately separate:

  * A *finding* is one concrete issue. Its severity says whether it BLOCKS
    touchless payment: 'error' blocks, 'warning'/'info' are advisory context.
    A finding's code names the issue; its source is who raised it — deterministic
    code, or the LLM judge.

  * A *category* is the single headline disposition the judge assigns to a held
    invoice — the human-facing "what is this?". The deterministic layer suggests
    categories from its findings, but the judge owns the final pick because some
    categories (FRAUD_SUSPECTED, near-DUPLICATE) are a read of the whole document
    that no rule can make.

  * A *level* is how alarming the hold is, independent of blocking. The $10K
    threshold blocks payment but is LOW alarm ("big, get a signature"); an
    unknown vendor blocks and is HIGH ("we have no relationship with these
    people"). Level drives triage and UI urgency, never the pay/hold decision.
"""

from enum import Enum


class Severity(str, Enum):
    INFO = "info"        # context, no bearing on payment
    WARNING = "warning"  # advisory — noted, does not block
    ERROR = "error"      # blocks touchless payment

    @property
    def blocks(self) -> bool:
        return self is Severity.ERROR


class Level(str, Enum):
    """Alarm of a held invoice — how hard a human should look. Ordered."""
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_LEVEL_RANK = {Level.INFO: 0, Level.LOW: 1, Level.MEDIUM: 2, Level.HIGH: 3, Level.CRITICAL: 4}


def max_level(a: Level, b: Level) -> Level:
    return a if _LEVEL_RANK[a] >= _LEVEL_RANK[b] else b


class ReviewCategory(str, Enum):
    """The headline reason a held invoice needs a human. The judge assigns one."""
    DUPLICATE = "duplicate"              # looks like a re-bill of an invoice we've seen
    OVER_BUDGET = "over_budget"          # quantity/amount exceeds what the PO authorized
    UNKNOWN_VENDOR = "unknown_vendor"    # vendor isn't in the master at all
    MISSING_PO = "missing_po"            # known vendor, plausible items, no PO on file (likely ours to fix)
    FRAUD_SUSPECTED = "fraud_suspected"  # the document reads like a scam (judge-only)
    DATA_INTEGRITY = "data_integrity"    # negative quantities, broken arithmetic, impossible fields
    OVERSIZE = "oversize"                # over the auto-pay ceiling; needs a signature
    LEGIBILITY = "legibility"            # couldn't read it confidently enough to pay


# Finding codes the deterministic layer raises. Kept as constants so validation
# and the suggested-category mapping can't drift apart on a typo.
class Code(str, Enum):
    UNKNOWN_VENDOR = "unknown_vendor"
    VENDOR_INACTIVE = "vendor_inactive"
    NO_PO = "no_po"
    PO_NOT_OPEN = "po_not_open"
    PO_VENDOR_MISMATCH = "po_vendor_mismatch"
    ITEM_NOT_ON_PO = "item_not_on_po"
    QTY_OVER_AUTHORIZED = "qty_over_authorized"
    PRICE_MISMATCH = "price_mismatch"
    CURRENCY_MISMATCH = "currency_mismatch"
    ARITHMETIC_MISMATCH = "arithmetic_mismatch"
    NEGATIVE_QUANTITY = "negative_quantity"
    NEGATIVE_PRICE = "negative_price"
    MISSING_FIELD = "missing_field"
    DUE_DATE_INVALID = "due_date_invalid"
    OVERSIZE = "oversize"
    DUPLICATE = "duplicate"
    ILLEGIBLE = "illegible"


# Which category each deterministic code argues for. The judge sees this as a
# suggestion, then decides — it may collapse several into FRAUD_SUSPECTED.
CODE_CATEGORY: dict[Code, ReviewCategory] = {
    Code.UNKNOWN_VENDOR: ReviewCategory.UNKNOWN_VENDOR,
    Code.VENDOR_INACTIVE: ReviewCategory.UNKNOWN_VENDOR,
    Code.NO_PO: ReviewCategory.MISSING_PO,
    Code.PO_NOT_OPEN: ReviewCategory.MISSING_PO,
    Code.PO_VENDOR_MISMATCH: ReviewCategory.MISSING_PO,
    Code.ITEM_NOT_ON_PO: ReviewCategory.OVER_BUDGET,
    Code.QTY_OVER_AUTHORIZED: ReviewCategory.OVER_BUDGET,
    Code.PRICE_MISMATCH: ReviewCategory.OVER_BUDGET,
    Code.CURRENCY_MISMATCH: ReviewCategory.DATA_INTEGRITY,
    Code.ARITHMETIC_MISMATCH: ReviewCategory.DATA_INTEGRITY,
    Code.NEGATIVE_QUANTITY: ReviewCategory.DATA_INTEGRITY,
    Code.NEGATIVE_PRICE: ReviewCategory.DATA_INTEGRITY,
    Code.MISSING_FIELD: ReviewCategory.DATA_INTEGRITY,
    Code.DUE_DATE_INVALID: ReviewCategory.DATA_INTEGRITY,
    Code.OVERSIZE: ReviewCategory.OVERSIZE,
    Code.DUPLICATE: ReviewCategory.DUPLICATE,
    Code.ILLEGIBLE: ReviewCategory.LEGIBILITY,
}
