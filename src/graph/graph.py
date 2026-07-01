"""LangGraph pipeline: ingestion -> validation -> approval (+critique) -> payment.

Duplicates short-circuit to rejection_log. High-value invoices loop through
critique before finalising. Foreign-currency invoices exit via needs_review.
"""

import logging
from typing import Literal

from langgraph.graph import END, START, StateGraph


from src.agents.approval_agent import run_approval, run_critique
from src.agents.ingestion_agent import run_ingestion
from src.agents.payment_agent import run_payment, run_rejection_log
from src.agents.validation_agent import run_validation
from src.graph.state import InvoiceState

logger = logging.getLogger(__name__)


# ── Conditional edge functions ────────────────────────────────────────────────


def route_after_validation(
    state: InvoiceState,
) -> Literal["rejection_log", "approval"]:
    """Short-circuit to rejection_log on confirmed duplicate; else approval."""
    if state.get("is_duplicate"):
        logger.info("Routing: duplicate detected -> rejection_log")
        return "rejection_log"
    return "approval"


def route_after_approval(
    state: InvoiceState,
) -> Literal["critique", "payment", "rejection_log"]:
    """After the approval node, decide whether to critique, pay, or reject.

    The approval node signals escalation by leaving final_decision unset and
    prefixing approval_reasoning with '[PRELIMINARY:'. The graph routes to
    critique whenever it sees that prefix, regardless of invoice amount —
    so unknown-vendor, error-flag, and low-confidence escalations all reach
    Senior Audit, not just high-value invoices.
    """
    final_decision = state.get("final_decision")

    # Approval node has finalised — route to payment or rejection.
    if final_decision is not None:
        if final_decision in ("approved", "needs_review"):
            logger.info("Routing: final_decision=%s -> payment", final_decision)
            return "payment"
        logger.info("Routing: final_decision=rejected -> rejection_log")
        return "rejection_log"

    # Approval node escalated (preliminary decision) — always go to critique.
    approval_reasoning = state.get("approval_reasoning") or ""
    if approval_reasoning.startswith("[PRELIMINARY:"):
        logger.info("Routing: preliminary decision detected -> critique")
        return "critique"

    # Fallback — should not reach here under normal operation.
    logger.warning(
        "Routing: unexpected state after approval, defaulting to rejection_log"
    )
    return "rejection_log"


# ── Graph builder ─────────────────────────────────────────────────────────────


def build_graph() -> StateGraph:
    """Construct and compile the LangGraph invoice processing pipeline."""
    builder = StateGraph(InvoiceState)

    # Register nodes
    builder.add_node("ingestion", run_ingestion)
    builder.add_node("validation", run_validation)
    builder.add_node("approval", run_approval)
    builder.add_node("critique", run_critique)
    builder.add_node("payment", run_payment)
    builder.add_node("rejection_log", run_rejection_log)

    # Fixed edges
    builder.add_edge(START, "ingestion")
    builder.add_edge("ingestion", "validation")

    # Conditional edges
    builder.add_conditional_edges(
        "validation",
        route_after_validation,
        {"rejection_log": "rejection_log", "approval": "approval"},
    )
    builder.add_conditional_edges(
        "approval",
        route_after_approval,
        {
            "critique": "critique",
            "payment": "payment",
            "rejection_log": "rejection_log",
        },
    )
    builder.add_edge("critique", "approval")

    # Terminal edges
    builder.add_edge("payment", END)
    builder.add_edge("rejection_log", END)

    return builder.compile()


# Module-level compiled graph (cached after first import)
_graph = None


def get_graph() -> StateGraph:
    """Return the compiled graph, building it on first call."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def _defaults(initial_state: InvoiceState) -> InvoiceState:
    """Merge caller-supplied state with pipeline defaults."""
    return {
        "llm_calls": 0,
        "total_tokens": 0,
        "review_count": 0,
        "is_duplicate": False,
        "validation_flags": [],
        "final_decision": None,
        "payment_result": None,
        "audit_log": [],
        "error": None,
        **initial_state,
    }


def run_pipeline(initial_state: InvoiceState) -> InvoiceState:
    """Run the pipeline and return the final merged state."""
    return get_graph().invoke(_defaults(initial_state))


def stream_pipeline(initial_state: InvoiceState):
    """Yield (node_name, accumulated_state) after each node completes.

    Suitable for Streamlit: render each stage as soon as its node finishes.

    Reducer fields (audit_log uses operator.add) are merged manually here
    because the raw stream updates contain only each node's new entries —
    LangGraph applies reducers internally for invoke() but not for the raw
    update dicts we receive in stream_mode="updates".
    """
    state = _defaults(initial_state)
    for event in get_graph().stream(state, stream_mode="updates"):
        for node_name, updates in event.items():
            # Manually apply operator.add for audit_log
            if "audit_log" in updates:
                updates = {
                    **updates,
                    "audit_log": state.get("audit_log", []) + updates["audit_log"],
                }
            state = {**state, **updates}
            yield node_name, state
