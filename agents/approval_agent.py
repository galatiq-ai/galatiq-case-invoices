"""Approval Agent — rule-based VP-level review with LLM-driven critique loop.

Makes approval decisions based on:
1. Hard rules (amount thresholds, validation failures)
2. LLM-driven reasoning for borderline cases
3. Self-critique loop: re-reads its own decision and revises if needed
"""

from __future__ import annotations

import re
from pathlib import Path

from src.database import is_known_vendor, is_invoice_already_processed, mark_invoice_processed, log_agent_event
from src.audit import record_stage_event
from src.state import (
    ApprovalDecision,
    ExtractedInvoice,
    ValidationResult,
)
from src.tools import write_trace, get_error_context

import json
from src.config import get_llm
import src.config as config

# Thresholds
HIGH_VALUE_THRESHOLD = 10_000.0
MAX_CORRECTION_LOOPS = 2


def _dedupe_keep_order(values: list[str]) -> list[str]:
    """Return values with duplicates removed while preserving order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _build_correction_hints(
    invoice: ExtractedInvoice,
    validation: ValidationResult | None,
    decision: ApprovalDecision,
) -> list[str]:
    """Generate actionable hints that can improve a re-ingest pass."""
    hints: list[str] = []

    if validation:
        for check in validation.inventory_checks:
            status = (check.status or "").lower()
            item_name = check.item or "unknown item"
            if status == "unknown_item":
                hints.append(
                    f"Re-check the invoice body for the exact item name; '{item_name}' may have been normalized incorrectly."
                )
            elif status == "stock_mismatch":
                hints.append(
                    f"Verify the quantity for '{item_name}' against the invoice line item; the current extraction may be off."
                )
            elif status == "out_of_stock":
                hints.append(
                    f"Confirm whether '{item_name}' is a real product name or an OCR/typing error in the invoice."
                )

        for error in validation.integrity_errors:
            issue = error.issue.lower()
            if "negative quantity" in issue:
                hints.append("Preserve the signed quantity exactly; do not coerce negative quantities to positive.")
            elif "missing due date" in issue:
                hints.append("Re-read the header and footer for the due date and normalize it to YYYY-MM-DD.")
            elif "invoice number" in issue:
                hints.append("Look for the invoice number near the document header and keep the full alphanumeric token.")
            elif "vendor" in issue:
                hints.append("Extract the vendor name from the invoice header and preserve its full company name.")

    if invoice.date and not invoice.due_date:
        hints.append("Look for a due date in the invoice header, footer, or payment terms section.")

    return _dedupe_keep_order(hints)


def approve_node(state: dict) -> dict:
    """LangGraph node: make approval decision with rule engine + critique loop."""
    extracted_data = state.get("extracted_invoice")
    validation_data = state.get("validation_result")

    if not extracted_data:
        state["error"] = "No extracted invoice data for approval"
        return state

    invoice = ExtractedInvoice(**extracted_data)
    validation = ValidationResult(**validation_data) if validation_data else None
    prev_decision_data = state.get("approval_decision")
    prev_correction_count = prev_decision_data.get("correction_count", 0) if prev_decision_data else 0

    # --- Phase 1: Rule-based decision ---
    decision = _rule_based_decision(invoice, validation)
    
    if prev_decision_data:
        decision.correction_count = prev_correction_count + 1

    # --- Phase 2: LLM critique loop (simulated) ---
    decision = _critique_loop(invoice, validation, decision)

    # --- Phase 3: Enforce hard rejections ---
    decision = _enforce_hard_rules(invoice, validation, decision)
    decision.correction_hints = _build_correction_hints(invoice, validation, decision)

    # Record the decision
    state["approval_decision"] = decision.model_dump()
    write_trace(state, "approval_agent")

    try:
        log_agent_event(
            invoice_number=invoice.invoice_number,
            agent="approval_agent",
            status="decided",
            decision=decision.decision,
            flags="; ".join(decision.required_actions[:3]),
            total=invoice.total,
            vendor=invoice.vendor,
        )
    except Exception:
        # Do not let logging failures affect pipeline
        pass

    # Also write the structured audit event (best-effort) so approval shows up
    try:
        file_path = state.get("file_path", "")
        file_name = Path(file_path).name if file_path else ""
        record_stage_event(
            invoice_number=invoice.invoice_number,
            file_name=file_name,
            stage="approve",
            status=(decision.decision if decision.decision in ("approved", "hold", "rejected") else "unknown"),
            decision=decision.decision,
            reason=(decision.reason or ""),
            flags=("; ".join(decision.required_actions[:3]) if decision.required_actions else ""),
            actor="approval_agent",
            metadata={
                "correction_hints": getattr(decision, "correction_hints", []),
                "correction_count": getattr(decision, "correction_count", 0),
            },
            total=invoice.total,
            vendor=invoice.vendor or "",
        )
    except Exception:
        # Best-effort: do not break the pipeline on audit write failures
        pass

    action_icon = {"approved": "[PASS]", "rejected": "[FAIL]", "hold": "[HOLD]", "pending": "[?]"}
    print(f"  [APPROVAL] {invoice.invoice_number} | {action_icon.get(decision.decision, '?')} "
          f"{decision.decision.upper()} | {decision.reason[:80]}")

    if config.VERBOSE:
        print(f"    [APPROVAL DETAIL] Required actions: {decision.required_actions}")
        print(f"    [APPROVAL DETAIL] Full reason: {decision.reason}")

    return state


def _rule_based_decision(invoice: ExtractedInvoice, validation: ValidationResult | None) -> ApprovalDecision:
    """Phase 1: Apply hard rules to determine initial decision."""
    reasons = []
    required_actions = []
    decision = "approved"

    # Rule 1: High-value invoices
    if invoice.total > HIGH_VALUE_THRESHOLD:
        reasons.append(f"Invoice total ${invoice.total:,.2f} exceeds ${HIGH_VALUE_THRESHOLD:,.2f} threshold")
        required_actions.append("Additional VP scrutiny required for high-value invoice")
        # Don't auto-reject, but flag for scrutiny

    # Rule 2: Validation failures
    if validation and not validation.passed:
        # Categorize the failures
        stock_issues = [c for c in validation.inventory_checks if c.status in ("stock_mismatch", "out_of_stock")]
        unknown_items = [c for c in validation.inventory_checks if c.status == "unknown_item"]
        integrity_errors = [e for e in validation.integrity_errors if e.severity == "error"]

        if unknown_items:
            items_str = ", ".join(c.item for c in unknown_items)
            reasons.append(f"Unknown inventory items: {items_str}")
            required_actions.append(f"Verify items {items_str} with procurement team")

        if stock_issues:
            for c in stock_issues:
                reasons.append(c.message)
            required_actions.append("Contact vendor to resolve stock discrepancies")

        if integrity_errors:
            for e in integrity_errors:
                reasons.append(f"Data integrity error: {e.issue}")
            required_actions.append("Request corrected invoice from vendor")

        # A validation failure always leads to rejection or hold
        if decision != "hold":
            decision = "hold"

    # Rule 3: Known vendor check
    if not is_known_vendor(invoice.vendor):
        reasons.append(f"Unknown vendor '{invoice.vendor}' — not in approved vendor list")
        required_actions.append("Vendor onboarding and verification required")
        if decision == "approved":
            decision = "hold"

    # Rule 4: Currency check (mock payment only handles USD)
    if invoice.currency and invoice.currency.upper() != "USD":
        reasons.append(f"Currency '{invoice.currency}' not supported — manual FX handling required")
        required_actions.append("Route to treasury for foreign currency processing")
        if decision == "approved":
            decision = "hold"

    # Rule 5: Duplicate invoice check
    existing_status = is_invoice_already_processed(invoice.invoice_number)
    if existing_status:
        reasons.append(f"Invoice {invoice.invoice_number} already processed (status: {existing_status})")
        required_actions.append("Verify this is not a duplicate payment")
        decision = "hold"

    # Rule 6: Suspicious urgency language
    urgency_patterns = [
        r'urgent.*pay', r'pay.*immediately', r'avoid.*penalt',
        r'wire.*transfer.*prefer', r'immediate.*payment',
        r'penalt',  # catches "penalties" with typos
    ]
    for pat in urgency_patterns:
        if re.search(pat, invoice.raw_text, re.IGNORECASE):
            reasons.append("Invoice contains urgency/pressure language — possible fraud indicator")
            required_actions.append("Manual fraud review required")
            if decision == "approved":
                decision = "hold"
            break

    # If no issues found, approve
    if decision == "approved" and not reasons:
        reasons.append("All validation checks passed")

    reason_text = "; ".join(reasons) if reasons else "No specific reason"
    return ApprovalDecision(
        decision=decision,
        reason=reason_text,
        required_actions=required_actions,
        correction_count=0,
    )


def _critique_loop(
    invoice: ExtractedInvoice,
    validation: ValidationResult | None,
    decision: ApprovalDecision,
) -> ApprovalDecision:
    """Phase 2: LLM-driven critique of the initial rule-based decision.

    Falls back to rule-based critique if no LLM is available.
    """
    llm = get_llm()
    if llm is None:
        return _rule_based_critique(invoice, validation, decision)

    prompt_path = Path(__file__).resolve().parent.parent / "docs" / "prompt_templates.md"
    critique_prompt = _read_critique_prompt(prompt_path)

    val_summary = "No validation data."
    if validation:
        val_summary = validation.summary or ("PASS" if validation.passed else "FAIL")

    items_str = ", ".join(f"{li.item} x{li.qty}" for li in invoice.items) or "No items"

    filled_prompt = critique_prompt.format(
        invoice_number=invoice.invoice_number,
        vendor=invoice.vendor,
        total=f"{invoice.total:,.2f}",
        currency=invoice.currency or "USD",
        items=items_str,
        validation_summary=val_summary,
    )

    user_message = (
        f"{filled_prompt}\n\n"
        f"Draft decision: {decision.decision.upper()}\n"
        f"Draft reason: {decision.reason}\n\n"
        "Review this draft. Output a JSON object with:\n"
        '- "decision": "approved", "rejected", or "hold"\n'
        '- "reason": string (your full reasoning)\n'
        '- "required_actions": array of strings\n'
        '- "correction_hints": array of strings to help the next extraction pass\n'
        "Output valid JSON only, no markdown."
    )

    try:
        from langchain_core.messages import HumanMessage
        from src.llm_binding import invoke_model_with_schema

        messages = [HumanMessage(content=user_message)]
        schema_hint = {"fields": ["decision", "reason", "required_actions", "correction_hints"]}

        try:
            data = invoke_model_with_schema(llm, messages, schema=schema_hint)
        except Exception:
            # Fallback to plain invoke + parse
            try:
                resp = llm.invoke(messages)
                data = json.loads((getattr(resp, "content", str(resp))).strip())
            except Exception as e:
                decision.reason += f" | (LLM critique unavailable: {type(e).__name__})"
                return _rule_based_critique(invoice, validation, decision)

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                decision.reason += " | (LLM critique returned non-JSON)"
                return _rule_based_critique(invoice, validation, decision)

        llm_decision = (data or {}).get("decision", "").lower()
        llm_reason = (data or {}).get("reason", "")
        llm_actions = (data or {}).get("required_actions", []) or []
        llm_hints = (data or {}).get("correction_hints", []) or []

        if llm_decision not in ("approved", "rejected", "hold"):
            decision.reason += " | (LLM returned invalid decision)"
            return _rule_based_critique(invoice, validation, decision)

        decision.decision = llm_decision
        decision.reason = f"{decision.reason} | LLM CRITIQUE: {llm_reason}"
        for action in llm_actions:
            if action not in decision.required_actions:
                decision.required_actions.append(action)
        if isinstance(llm_hints, list):
            decision.correction_hints = _dedupe_keep_order(
                decision.correction_hints + [str(h) for h in llm_hints if str(h).strip()]
            )
        return decision
    except Exception as e:
        decision.reason += f" | (LLM critique unavailable: {type(e).__name__})"
        return _rule_based_critique(invoice, validation, decision)


def _rule_based_critique(
    invoice: ExtractedInvoice,
    validation: ValidationResult | None,
    decision: ApprovalDecision,
) -> ApprovalDecision:
    """Fallback: the original rule-based critique logic."""
    current = decision
    if current.decision == "approved" and not is_known_vendor(invoice.vendor):
        current.decision = "hold"
        current.reason += " | CRITIQUE: New vendor — revising to hold"
        if "Vendor onboarding" not in current.required_actions:
            current.required_actions.append("Vendor onboarding and verification")
        return current
    if current.decision == "rejected" and validation:
        severity_errors = [e for e in validation.integrity_errors if e.severity == "error"]
        failed_inventory = [c for c in validation.inventory_checks if c.status not in ("ok", "")]
        if not severity_errors and all(c.status == "unknown_item" for c in failed_inventory):
            current.reason += " | CRITIQUE: Downgrading to hold — issues may be resolvable"
            current.decision = "hold"
    if current.decision == "approved" and invoice.total > HIGH_VALUE_THRESHOLD:
        current.reason += " | CRITIQUE: High-value invoice reviewed and approved"
        if "Additional VP scrutiny" not in current.required_actions:
            current.required_actions.append("Additional VP scrutiny for high-value invoice")
    return current


def _read_critique_prompt(prompt_path: Path) -> str:
    """Read the approval critique section from docs/prompt_templates.md."""
    if not prompt_path.exists():
        return (
            "You are a VP approver reviewing invoice {invoice_number} from {vendor}. "
            "Total: {total} {currency}. Items: {items}. "
            "Validation: {validation_summary}. Decide: approved, rejected, or hold."
        )
    content = prompt_path.read_text(encoding="utf-8")
    match = re.search(r"## Approval Critique Prompt\n(.+?)(?=\n## |\Z)", content, re.DOTALL)
    return match.group(1).strip() if match else content


def _enforce_hard_rules(
    invoice: ExtractedInvoice,
    validation: ValidationResult | None,
    decision: ApprovalDecision,
) -> ApprovalDecision:
    """Phase 3: Hard rules that cannot be overridden by critique.

    These are non-negotiable rejections.
    """
    # Hard rule: Negative quantities → auto-reject
    if validation:
        for e in validation.integrity_errors:
            if "negative quantity" in e.issue.lower() or "negative total" in e.issue.lower():
                decision.decision = "rejected"
                decision.reason = (
                    f"HARD REJECTION: Data integrity violation — {e.issue}. "
                    "Invoice contains invalid data and cannot be processed."
                )
                decision.required_actions = ["Vendor must issue corrected invoice"]
                return decision

        # Hard rule: Out-of-stock items (zero stock) → reject as suspicious/fraudulent
        for c in validation.inventory_checks:
            if c.status == "out_of_stock":
                decision.decision = "rejected"
                decision.reason = (
                    f"HARD REJECTION: Item '{c.item}' has zero stock — "
                    "possible fraudulent entry. Vendor and invoice require investigation."
                )
                decision.required_actions = ["Escalate to fraud investigation team"]
                return decision

    return decision
    return decision
