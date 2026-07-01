"""Named constants for the invoice processing pipeline.

All tunable policy values live here so agent logic never embeds magic numbers.
"""

# --- Approval thresholds ---
# Invoices above this amount trigger the critique loop in the approval agent.
HIGH_VALUE_THRESHOLD: float = 10_000.0

# Maximum number of critique-then-re-approve cycles before finalising the
# decision regardless of outstanding concerns.
MAX_REVIEW_ROUNDS: int = 1
MAX_TOOL_ITERATIONS: int = 5  # safety cap on approval agent's tool-use loop

# --- Paths ---
INVOICE_DIR: str = "data/invoices"
LOG_DIR: str = "runs"
DB_PATH: str = "acme.db"  # single SQLite file: inventory, normalized schema, precedent

# --- Validation tolerances ---
# Fractional tolerance when comparing a stated invoice total against the
# sum of its own line items (handles rounding in tax computations).
TOTAL_MISMATCH_TOLERANCE: float = 0.05  # dollars

# Price mismatch: flag if invoiced unit price differs from DB expected price
# by more than this fraction (e.g. 0.20 = 20% deviation allowed before flagging).
PRICE_MISMATCH_TOLERANCE: float = 0.20

# --- LLM models ---
NVIDIA_MODEL: str = "meta/llama-3.1-8b-instruct"
GROK_MODEL: str = "grok-3"

# --- Precedent learning ---
# Minimum consistent decisions before a flag-pattern precedent is auto-applied.
PRECEDENT_CONFIDENCE_THRESHOLD: int = 10
# Invoices above this amount are never auto-decided by precedent — always LLM.
PRECEDENT_MAX_AMOUNT: float = 5_000.0

# --- Approval confidence gate ---
# VP decisions below this confidence score are escalated to critique even when
# the invoice is below HIGH_VALUE_THRESHOLD and has no error flags.
MIN_APPROVAL_CONFIDENCE: float = 0.65

# --- Extraction ---
# Second LLM pass fires when extraction confidence falls below this threshold.
EXTRACTION_CONFIDENCE_THRESHOLD: float = 0.80
# After a schema self-correction, confidence is capped at this value.
SCHEMA_ERROR_CONFIDENCE_CAP: float = 0.75

# --- Vendor trust thresholds ---
# Minimum submissions before a vendor can be classified as trusted or flagged.
MIN_VENDOR_SUBMISSIONS: int = 3
# Approval rate at or above this marks a vendor as trusted.
TRUSTED_VENDOR_APPROVAL_RATE: float = 0.80
# Rejection rate at or above this (with enough submissions) marks a vendor as high-risk.
VENDOR_REJECTION_RATE_THRESHOLD: float = 0.50
