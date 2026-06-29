from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from agents.ingestion_agent import IngestionAgent
from agents.validation_agent import ValidationAgent
from agents.approval_agent import ApprovalAgent
from agents.payment_agent import PaymentAgent
from models.invoice_models import Invoice, ValidationResult, ApprovalResult


class InvoiceWorkflowState(TypedDict):
    invoice_path: str
    invoice: Optional[Invoice]
    validation_result: Optional[ValidationResult]
    approval_result: Optional[ApprovalResult]
    payment_result: Optional[dict]


def ingestion_node(state: InvoiceWorkflowState) -> InvoiceWorkflowState:
    agent = IngestionAgent()
    state["invoice"] = agent.process(state["invoice_path"])
    return state


def validation_node(state: InvoiceWorkflowState) -> InvoiceWorkflowState:
    agent = ValidationAgent()
    state["validation_result"] = agent.validate(state["invoice"])
    return state


def approval_node(state: InvoiceWorkflowState) -> InvoiceWorkflowState:
    agent = ApprovalAgent()
    state["approval_result"] = agent.decide(
        state["invoice"],
        state["validation_result"]
    )
    return state


def payment_node(state: InvoiceWorkflowState) -> InvoiceWorkflowState:
    agent = PaymentAgent()
    state["payment_result"] = agent.process_payment(
        state["invoice"],
        state["approval_result"]
    )
    return state


def validation_router(state: InvoiceWorkflowState) -> str:
    if state["validation_result"].passed:
        return "approval"

    return "end"


def build_invoice_workflow():
    graph = StateGraph(InvoiceWorkflowState)

    graph.add_node("ingestion", ingestion_node)
    graph.add_node("validation", validation_node)
    graph.add_node("approval", approval_node)
    graph.add_node("payment", payment_node)

    graph.set_entry_point("ingestion")

    graph.add_edge("ingestion", "validation")

    graph.add_conditional_edges(
        "validation",
        validation_router,
        {
            "approval": "approval",
            "end": END,
        }
    )

    graph.add_edge("approval", "payment")
    graph.add_edge("payment", END)

    return graph.compile()