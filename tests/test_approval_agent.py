"""Unit tests for the approval agent.

Uses mocked LLM responses — never calls the real API.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.approval_agent import run_approval, run_critique
from src.graph.state import ApprovalDecision, CritiqueOutput
from src.ops_db import init_db, reset_approved_quantities


@pytest.fixture(autouse=True)
def fresh_db():
    init_db()
    reset_approved_quantities()
    yield


def _make_approval_decision(
    decision: str = "approved", confidence: float = 0.9
) -> ApprovalDecision:
    return ApprovalDecision(
        decision=decision,
        reasoning=f"Test reasoning — {decision}",
        key_factors=["key factor 1", "key factor 2"],
        confidence=confidence,
    )


def _make_critique() -> CritiqueOutput:
    return CritiqueOutput(
        critique="Test critique: the VP missed flag X.",
        concerns=["concern 1"],
    )


def _base_state(
    amount: float = 5000.0, flags: list = None, review_count: int = 0
) -> dict:
    return {
        "extracted_data": {
            "invoice_number": "INV-TEST",
            "vendor": "Test Vendor",
            "amount": amount,
            "currency": "USD",
            "items": [{"name": "WidgetA", "qty": 5, "unit_price": 250.0}],
            "due_date": "2026-02-01",
            "extraction_warnings": [],
        },
        "validation_flags": flags or [],
        "review_count": review_count,
        "llm_calls": 0,
        "total_tokens": 0,
        "approval_reasoning": "",
        "critique_notes": "",
    }


# ── Low-value invoice: single pass, final_decision set immediately ─────────────


@patch("src.agents.approval_agent.get_llm")
def test_low_value_invoice_single_pass(mock_get_llm):
    """Invoice under HIGH_VALUE_THRESHOLD gets final_decision in one pass."""
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm
    # bind_tools returns a mock that doesn't produce tool_calls
    mock_with_tools = MagicMock()
    mock_llm.bind_tools.return_value = mock_with_tools
    ai_msg = MagicMock()
    ai_msg.tool_calls = []
    ai_msg.content = "approved"
    ai_msg.usage_metadata = {"total_tokens": 100}
    mock_with_tools.invoke.return_value = ai_msg

    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.return_value = _make_approval_decision("approved")

    state = _base_state(amount=5000.0)
    result = run_approval(state)

    assert result.get("final_decision") == "approved"
    assert "decision_reasoning" in result


@patch("src.agents.approval_agent.get_llm")
def test_high_value_invoice_first_pass_no_final_decision(mock_get_llm):
    """High-value invoice first pass does NOT set final_decision (awaits critique)."""
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_with_tools = MagicMock()
    mock_llm.bind_tools.return_value = mock_with_tools
    ai_msg = MagicMock()
    ai_msg.tool_calls = []
    ai_msg.content = "approve this"
    ai_msg.usage_metadata = {"total_tokens": 200}
    mock_with_tools.invoke.return_value = ai_msg

    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.return_value = _make_approval_decision("approved")

    state = _base_state(amount=15000.0, review_count=0)
    result = run_approval(state)

    # First pass on a high-value invoice: final_decision should NOT be set
    assert result.get("final_decision") is None
    assert "PRELIMINARY" in result.get("approval_reasoning", "")


@patch("src.agents.approval_agent.get_llm")
def test_high_value_invoice_second_pass_finalises(mock_get_llm):
    """Second pass (review_count=1) always sets final_decision."""
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_with_tools = MagicMock()
    mock_llm.bind_tools.return_value = mock_with_tools
    ai_msg = MagicMock()
    ai_msg.tool_calls = []
    ai_msg.content = "final approved"
    ai_msg.usage_metadata = {"total_tokens": 200}
    mock_with_tools.invoke.return_value = ai_msg

    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.return_value = _make_approval_decision("approved")

    state = _base_state(amount=15000.0, review_count=1)
    state["critique_notes"] = "Critique: checked and concerns resolved."
    result = run_approval(state)

    assert result.get("final_decision") == "approved"


# ── Approval agent error: fail safe to rejected ───────────────────────────────


@patch("src.agents.approval_agent.get_llm")
def test_approval_agent_error_fail_safe(mock_get_llm):
    """If the LLM call fails, approval defaults to rejected (not approved)."""
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm
    # Raise on both paths: direct invoke (no flags) and bind_tools invoke (with flags)
    mock_llm.invoke.side_effect = RuntimeError("LLM down")
    mock_with_tools = MagicMock()
    mock_llm.bind_tools.return_value = mock_with_tools
    mock_with_tools.invoke.side_effect = RuntimeError("LLM down")

    state = _base_state(amount=5000.0)
    result = run_approval(state)

    assert result.get("final_decision") == "rejected"


# ── Critique node ─────────────────────────────────────────────────────────────


@patch("src.agents.approval_agent.get_llm")
def test_critique_never_outputs_verdict(mock_get_llm):
    """Critique output is text only — no decision field in its return dict."""
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.return_value = _make_critique()

    state = _base_state(amount=15000.0)
    state["approval_reasoning"] = "[PRELIMINARY: approved | awaiting critique]\napprove"
    result = run_critique(state)

    assert "critique_notes" in result
    assert "final_decision" not in result  # critic never decides
    assert result.get("review_count", 0) == 1  # incremented


@patch("src.agents.approval_agent.get_llm")
def test_critique_increments_review_count(mock_get_llm):
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.return_value = _make_critique()

    state = _base_state(amount=15000.0, review_count=0)
    state["approval_reasoning"] = "[PRELIMINARY]\nreasoning"
    result = run_critique(state)
    assert result["review_count"] == 1
