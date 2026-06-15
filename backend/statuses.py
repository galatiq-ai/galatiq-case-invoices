"""The invoice state machine.

Status is the invoice's durable *disposition* — where it rests — not which
pipeline stage is running (that's recorded per step in invoice_trace). Keeping
status coarse means the pipeline can grow stages without touching this machine.

  RECEIVED ──► PROCESSING ──► APPROVED ──► PAID          (touchless)
                   │              ▲
                   ├──► NEEDS_REVIEW ──┘   human: approve & pay
                   ├──► SUPERSEDED                        exact-duplicate auto-dedup
                   └──► FAILED                            processing error (defensive)

The outcome space is deliberately binary: an invoice either pays (touchless, or
after a human clears review) or it waits on a human. There is no automated
REJECTED — the system never declines on its own; it routes anything it can't
clear to NEEDS_REVIEW with a category explaining why (backend/review.py).

Transitions are asserted: an illegal move is an impossible() state, never a
silent write. The LLM never drives a transition — only deterministic code does;
the judge's verdict is an input the gate reads, not a write it makes.
"""

from enum import Enum

from .impossible import impossible


class Status(str, Enum):
    RECEIVED = "received"          # ingested, not yet processed
    PROCESSING = "processing"      # pipeline running (extract … finalize)
    NEEDS_REVIEW = "needs_review"  # durable: waiting on a human
    APPROVED = "approved"          # cleared to pay (by the gate, or a human) — payment in flight
    PAID = "paid"                  # terminal: payment succeeded
    SUPERSEDED = "superseded"      # terminal: exact duplicate of a processed invoice
    FAILED = "failed"              # terminal: unrecoverable processing error


TERMINAL: frozenset[Status] = frozenset(
    {Status.PAID, Status.SUPERSEDED, Status.FAILED}
)

# Every active (non-terminal) state may also fail, so FAILED is folded in below.
_ALLOWED: dict[Status, set[Status]] = {
    Status.RECEIVED:     {Status.PROCESSING},
    Status.PROCESSING:   {Status.APPROVED, Status.NEEDS_REVIEW, Status.SUPERSEDED},
    Status.NEEDS_REVIEW: {Status.APPROVED},  # human clears review → APPROVED → PAID
    Status.APPROVED:     {Status.PAID},
    Status.PAID:         set(),
    Status.SUPERSEDED:   set(),
    Status.FAILED:       set(),
}
for _s, _targets in _ALLOWED.items():
    if _s not in TERMINAL:
        _targets.add(Status.FAILED)


def can_transition(current: Status, new: Status) -> bool:
    return new in _ALLOWED[current]


def assert_transition(current: Status, new: Status) -> None:
    """Permit a legal transition; an illegal one is an impossible() state."""
    if not can_transition(current, new):
        impossible(
            f"illegal invoice status transition: {current.value} -> {new.value}",
            {
                "from": current.value,
                "to": new.value,
                "allowed": sorted(s.value for s in _ALLOWED[current]),
            },
        )
