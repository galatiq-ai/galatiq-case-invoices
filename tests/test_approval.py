"""Tests for the approval agent - rules, critique, hard rejections."""

from agents.approval_agent import (
    _build_correction_hints,
    _enforce_hard_rules,
    _rule_based_decision,
    approve_node,
)
from src.state import (
    ApprovalDecision,
    ExtractedInvoice,
    IntegrityCheck,
    InventoryCheck,
    LineItem,
    ValidationResult,
)


def _clean_invoice(total=5000.0):
    return ExtractedInvoice(
        invoice_number="INV-TEST",
        vendor="Widgets Inc.",
        items=[LineItem(item="WidgetA", qty=5, unit_price=1000.0)],
        total=total,
    )


def _clean_validation():
    return ValidationResult(
        invoice_number="INV-TEST",
        passed=True,
        summary="All checks passed",
        inventory_checks=[InventoryCheck(item="WidgetA", requested_qty=5, status="ok")],
    )


class TestRuleBasedDecision:
    def test_clean_invoice_approved(self):
        assert _rule_based_decision(_clean_invoice(), _clean_validation()).decision == "approved"

    def test_high_value_adds_scrutiny_action(self):
        decision = _rule_based_decision(_clean_invoice(total=15000.0), _clean_validation())
        assert any("scrutiny" in a.lower() for a in decision.required_actions)

    def test_stock_mismatch_triggers_hold(self):
        val = ValidationResult(
            invoice_number="INV-TEST",
            passed=False,
            inventory_checks=[InventoryCheck(
                item="GadgetX",
                requested_qty=20,
                available_stock=5,
                status="stock_mismatch",
                message="Requested 20, only 5",
            )],
        )
        assert _rule_based_decision(_clean_invoice(), val).decision == "hold"

    def test_duplicate_invoice_triggers_hold(self):
        from src.database import mark_invoice_processed

        mark_invoice_processed("INV-TEST", status="paid")
        assert _rule_based_decision(_clean_invoice(), _clean_validation()).decision == "hold"


class TestHardRules:
    def test_negative_quantity_forces_rejection(self):
        val = ValidationResult(
            invoice_number="INV-TEST",
            passed=False,
            integrity_errors=[IntegrityCheck(
                field="items[WidgetA].qty",
                issue="Negative quantity (-5) for item 'WidgetA'",
                severity="error",
            )],
        )
        draft = ApprovalDecision(decision="hold", reason="hold")
        result = _enforce_hard_rules(_clean_invoice(), val, draft)
        assert result.decision == "rejected"
        assert "HARD REJECTION" in result.reason

    def test_zero_stock_forces_rejection(self):
        val = ValidationResult(
            invoice_number="INV-TEST",
            passed=False,
            inventory_checks=[InventoryCheck(
                item="FakeItem",
                requested_qty=1,
                available_stock=0,
                status="out_of_stock",
                message="FakeItem has zero stock",
            )],
        )
        draft = ApprovalDecision(decision="hold", reason="hold")
        result = _enforce_hard_rules(_clean_invoice(), val, draft)
        assert result.decision == "rejected"

    def test_correction_hints_generated_for_validation_issues(self):
        val = ValidationResult(
            invoice_number="INV-TEST",
            passed=False,
            inventory_checks=[InventoryCheck(
                item="GadgetX",
                requested_qty=20,
                available_stock=5,
                status="stock_mismatch",
                message="Requested 20, only 5",
            )],
        )
        draft = ApprovalDecision(decision="hold", reason="hold")
        hints = _build_correction_hints(_clean_invoice(), val, draft)
        assert hints
        assert any("quantity" in hint.lower() for hint in hints)

    def test_approve_node_persists_correction_hints(self):
        val = ValidationResult(
            invoice_number="INV-TEST",
            passed=False,
            inventory_checks=[InventoryCheck(
                item="SuperGizmo",
                requested_qty=1,
                status="unknown_item",
                message="Item not found",
            )],
        )
        state = {
            "extracted_invoice": _clean_invoice().model_dump(),
            "validation_result": val.model_dump(),
        }
        result = approve_node(state)
        hints = result["approval_decision"].get("correction_hints", [])
        assert hints
        assert any("item" in hint.lower() for hint in hints)
