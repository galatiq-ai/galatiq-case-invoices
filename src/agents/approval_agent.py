"""Stage 3: VP-level approval with an optional critique loop.

Two LangGraph nodes:
  run_approval  - VP reasoning pass with check_inventory tool access.
                  Escalates to critique on: high-value amount, error-severity
                  flags, low confidence, rejection decision, or approval with
                  active validation flags present.
  run_critique  - Independent Senior Auditor review of the VP's reasoning.
                  Has access to portfolio spend context the VP didn't see.
                  Never sets the verdict - only run_approval does.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from config import (
    HIGH_VALUE_THRESHOLD,
    MAX_REVIEW_ROUNDS,
    MAX_TOOL_ITERATIONS,
    MIN_APPROVAL_CONFIDENCE,
    TOTAL_MISMATCH_TOLERANCE,
)
from src.agents.validation_agent import check_inventory
from src.db.queries import get_vendor_risk_profile
from src.graph.state import ApprovalDecision, CritiqueOutput, InvoiceState
from src.ops_db import get_total_approved_spend
from src.llm_client import get_llm
from src.precedent_db import learn_from_decision, lookup_precedent

logger = logging.getLogger(__name__)

_APPROVAL_SYSTEM_PROMPT = """\
You are a VP of Finance at Acme Corp reviewing an invoice for approval.
Your job: carefully reason through whether to approve or reject this invoice,
then commit to a decision.

Mandatory reasoning steps:
1. Identify the vendor, amount, and line items.
2. Review every validation flag raised by the validation system.
   Flag rules you must follow — these are non-negotiable:
   - insufficient_stock: We do not have enough inventory to fulfill this item.
     REJECT the invoice. Do not approve orders we cannot physically ship.
   - out_of_stock: Item has zero units. REJECT — cannot fulfill.
   - unknown_item: Item is not in our catalog. REJECT — cannot process.
   - total_mismatch: The stated invoice total does not match its line items.
     REJECT. This flag is only raised when the discrepancy exceeds ${mismatch_tol:.2f} —
     any flagged invoice has already exceeded the rounding tolerance.
   - price_mismatch: Invoiced unit price deviates from our catalog price.
     REJECT if deviation is significant; use judgment for minor rounding.
   - invalid_quantity: A line item has a zero, negative, or missing quantity.
     REJECT — the order cannot be fulfilled as written.
   - revision_of_paid_invoice: This invoice revises one that was already paid.
     Approve only the incremental delta amount owed, not the full invoice total.
3. For invoices above ${threshold:,.0f}, apply extra scrutiny:
   - Does the amount seem reasonable for the items ordered?
   - Any red flags (pressure tactics, unusual payment terms)?
4. A vendor with no prior history is normal — judge only on the invoice itself.
   Reject only if there are concrete quality or fraud signals in the invoice content.
5. Decide: approve or reject.  Justify every key factor in your reasoning.

You have access to a check_inventory tool.  Stock and price checks have already
been run — only call the tool if a specific validation flag gives you a concrete
reason to re-examine a particular item.  If there are no flags, skip it.

Your reasoning must read as a VP's business judgment: vendor credibility, amount
reasonableness, risk signals, and any flag concerns.  Do not mention internal
system components (tools, validation pipeline, thresholds) in your reasoning.

Respond with a structured JSON containing:
  decision: "approved" | "rejected"
  reasoning: <full chain-of-thought>
  key_factors: [<2-4 bullet strings>]
  confidence: <float 0-1>
""".format(threshold=HIGH_VALUE_THRESHOLD, mismatch_tol=TOTAL_MISMATCH_TOLERANCE)

_CRITIQUE_SYSTEM_PROMPT = """\
You are an independent Audit Reviewer at Acme Corp.  The VP of Finance just
produced a reasoning trace and preliminary decision on an invoice.  Your job
is to critically review that reasoning — NOT to produce your own verdict.

You are given portfolio context (approved spend to date) as background intelligence
to assess whether this approval is proportionate given recent activity.  Use it to
inform your judgment — but do NOT cite figures, session totals, or cumulative
balances in your output.  Your critique must read as professional audit commentary:
vendor credibility, flag handling, risk signals, and reasoning quality.

Look for:
- Logical gaps or contradictions in the VP's reasoning.
- Validation flags that were not addressed or were dismissed too quickly.
- Any fraud risk signals that were overlooked.
- Whether the decision is proportionate to the risk and vendor profile.

Calibration rule: if the VP's reasoning is thorough and the invoice is
genuinely clean or low-risk, your correct output is to say so.  Do not
manufacture concerns to justify your existence.  Forced or speculative
critique on a well-reasoned approval is itself a reasoning failure.

OUTPUT ONLY a critique.  Do NOT output approve or reject.  Do NOT paraphrase
the VP's reasoning without adding new insight.  Report what the VP missed or
got wrong — or confirm the reasoning is sound if it is.
"""

_FINAL_PASS_ADDENDUM = """\
You previously reasoned about this invoice and a critic has now reviewed
your reasoning.  Read the critique below carefully.  Address each concern
raised in terms of THIS invoice's merits — vendor credibility, pricing,
stock levels, and risk signals.  Do not reference session totals, portfolio
balances, or cumulative figures.  Then produce your FINAL, binding decision.

If the critique raises valid concerns you cannot resolve, you may change your
decision.  If you disagree with the critique, explain why.

Critique:
{critique}
"""


# ── Tool-use loop helper ──────────────────────────────────────────────────────


def _run_with_tools(
    messages: list,
    llm_calls: int,
    total_tokens: int,
    has_flags: bool = False,
) -> tuple[str, int, int]:
    """Run the LLM in a tool-use loop until it produces a final response.

    check_inventory is only bound when there are validation flags — no point
    offering the tool to the LLM on a clean invoice with nothing to re-examine.

    Returns (final_content_str, updated_llm_calls, updated_total_tokens).
    """
    llm = get_llm()
    llm_with_tools = llm.bind_tools([check_inventory]) if has_flags else llm

    last_ai_content = ""
    for _ in range(MAX_TOOL_ITERATIONS):
        response: AIMessage = llm_with_tools.invoke(messages)
        llm_calls += 1
        last_ai_content = response.content

        usage = getattr(response, "usage_metadata", None)
        if usage:
            total_tokens += usage.get("total_tokens", 0)

        messages.append(response)

        if not response.tool_calls:
            return response.content, llm_calls, total_tokens

        # Execute each tool call and append results
        for tc in response.tool_calls:
            if tc["name"] == "check_inventory":
                result = check_inventory.invoke(tc["args"])
                messages.append(
                    ToolMessage(content=json.dumps(result), tool_call_id=tc["id"])
                )
                logger.debug("Tool check_inventory(%s) -> %s", tc["args"], result)

    # Reached MAX_TOOL_ITERATIONS — return the last AI response content (not a ToolMessage)
    logger.warning("Tool-use loop hit MAX_TOOL_ITERATIONS (%d)", MAX_TOOL_ITERATIONS)
    return last_ai_content, llm_calls, total_tokens


# ── VP approval pass ──────────────────────────────────────────────────────────


def _run_approval_pass(
    state: InvoiceState,
    critique_notes: str | None = None,
) -> tuple[ApprovalDecision, int, int]:
    """Call the LLM approval agent (with optional critique context).

    Returns (ApprovalDecision, llm_calls, total_tokens).
    """
    extracted: dict = state.get("extracted_data", {})
    flags: list = state.get("validation_flags", [])
    llm_calls: int = state.get("llm_calls", 0)
    total_tokens: int = state.get("total_tokens", 0)

    vendor = extracted.get("vendor", "Unknown")
    amount = extracted.get("amount", 0.0) or 0.0
    currency = extracted.get("currency", "USD")
    items = extracted.get("items", [])
    due_date = extracted.get("due_date", "Not stated")
    warnings = extracted.get("extraction_warnings", [])

    # Vendor risk profile from normalized DB — history of submissions, rates, flags
    profile = get_vendor_risk_profile(vendor)
    if profile["known_vendor"]:
        n = profile["total_submissions"]
        common = (
            ", ".join(f"{t} (x{cnt})" for t, cnt in profile["common_flags"])
            if profile["common_flags"]
            else "none on record"
        )
        # Flag low-sample rates so the LLM doesn't over-index on small counts.
        rate_note = " (small sample — treat rates with caution)" if n < 3 else ""
        vendor_context = (
            f"  Known vendor: {n} prior submission(s){rate_note}. "
            f"Approval rate {profile['approval_rate']:.0%}, "
            f"rejection rate {profile['rejection_rate']:.0%}. "
            f"Recurring quality flags (excluding process flags): {common}. "
            f"Last decision: {profile['last_decision'] or 'N/A'}."
        )
    else:
        vendor_context = (
            f"  First submission: no prior history for '{vendor}' on record."
        )

    # Split flags by severity so the VP sees what is blocking vs. advisory
    error_flags = [f for f in flags if f.get("severity") == "error"]
    warning_flags = [f for f in flags if f.get("severity") == "warning"]
    info_flags = [f for f in flags if f.get("severity") == "info"]

    def _fmt(lst: list) -> str:
        return (
            "\n".join(
                f"    - [{f['issue_type']}] {f['item']}: {f['detail']}" for f in lst
            )
            or "    (none)"
        )

    flag_section = (
        (
            f"  ERRORS (must be addressed to approve):\n{_fmt(error_flags)}\n"
            f"  WARNINGS (review carefully):\n{_fmt(warning_flags)}\n"
            f"  INFO (context only):\n{_fmt(info_flags)}"
        )
        if flags
        else "  (none)"
    )

    item_lines = (
        "\n".join(
            f"  - {i['name']}: qty={i['qty']}, unit_price={i.get('unit_price')}"
            for i in items
        )
        or "  (none)"
    )

    user_content = (
        f"**Invoice Details**\n"
        f"Vendor: {vendor}\n"
        f"Amount: {amount:,.2f} {currency}\n"
        f"Due Date: {due_date}\n"
        f"Line Items:\n{item_lines}\n\n"
        f"**Vendor History (from internal records):**\n{vendor_context}\n\n"
        f"**Extraction Warnings:**\n"
        + ("\n".join(f"  - {w}" for w in warnings) or "  (none)")
        + f"\n\n**Validation Flags:**\n{flag_section}"
    )

    messages: list = [
        SystemMessage(content=_APPROVAL_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]

    if critique_notes:
        messages.append(
            HumanMessage(content=_FINAL_PASS_ADDENDUM.format(critique=critique_notes))
        )

    raw_content, llm_calls, total_tokens = _run_with_tools(
        messages, llm_calls, total_tokens, has_flags=bool(flags)
    )

    # Parse structured output from the tool-use loop's final response
    llm = get_llm()
    structured_llm = llm.with_structured_output(ApprovalDecision)
    decision_messages = [
        SystemMessage(content=_APPROVAL_SYSTEM_PROMPT),
        HumanMessage(content=user_content),
    ]
    if critique_notes:
        decision_messages.append(
            HumanMessage(content=_FINAL_PASS_ADDENDUM.format(critique=critique_notes))
        )
    # Append the tool-loop conversation to give structured output the full context
    decision_messages.append(
        HumanMessage(
            content=f"Based on your analysis above, provide your structured decision:\n{raw_content}"
        )
    )
    decision: ApprovalDecision = structured_llm.invoke(decision_messages)
    llm_calls += 1

    return decision, llm_calls, total_tokens


# ── Critique pass ─────────────────────────────────────────────────────────────


def _run_critique_pass(
    state: InvoiceState,
    approval_reasoning: str,
) -> tuple[CritiqueOutput, int, int]:
    """Run the independent critic against the VP's first-pass reasoning.

    Gives the critic portfolio spend context the VP didn't see.
    Returns (CritiqueOutput, llm_calls, total_tokens).
    """
    # Extract VP's preliminary decision from structured prefix set by run_approval.
    # Format: "[PRELIMINARY: approved | awaiting critique]\n{reasoning}"
    preliminary_decision = "unknown"
    if "PRELIMINARY:" in approval_reasoning:
        try:
            preliminary_decision = (
                approval_reasoning.split(":")[1].split("|")[0].strip()
            )
        except (IndexError, ValueError):
            pass
    llm_calls: int = state.get("llm_calls", 0)
    total_tokens: int = state.get("total_tokens", 0)
    extracted: dict = state.get("extracted_data", {})
    flags: list = state.get("validation_flags", [])

    portfolio_spend = get_total_approved_spend()
    vendor = extracted.get("vendor", "Unknown")
    amount = extracted.get("amount", 0.0) or 0.0

    # Vendor history — gives critic additional context the VP may have overlooked
    profile = get_vendor_risk_profile(vendor)
    if profile["known_vendor"]:
        n = profile["total_submissions"]
        common = (
            ", ".join(f"{t} (x{cnt})" for t, cnt in profile["common_flags"])
            if profile["common_flags"]
            else "none on record"
        )
        rate_note = " (small sample)" if n < 3 else ""
        vendor_history = (
            f"{n} prior submission(s){rate_note}, "
            f"approval rate {profile['approval_rate']:.0%}, "
            f"quality flags: {common}"
        )
    else:
        vendor_history = "First submission: no prior history on record."

    flag_summary = (
        "\n".join(
            f"  - [{f.get('severity', '?').upper()} | {f['issue_type']}] {f['item']}: {f['detail']}"
            for f in flags
        )
        or "  (none)"
    )

    critic_context = (
        f"**Portfolio context (NOT seen by the VP):**\n"
        f"Total spend already approved in this session: ${portfolio_spend:,.2f}\n"
        f"Vendor history: {vendor_history}\n\n"
        f"**Invoice:** {vendor} | ${amount:,.2f}\n"
        f"**Validation flags:**\n{flag_summary}\n\n"
        f"**VP preliminary decision:** {preliminary_decision.upper()}\n\n"
        f"**VP reasoning trace:**\n{approval_reasoning}"
    )

    messages = [
        SystemMessage(content=_CRITIQUE_SYSTEM_PROMPT),
        HumanMessage(content=critic_context),
    ]

    llm = get_llm()
    structured_llm = llm.with_structured_output(CritiqueOutput)
    critique: CritiqueOutput = structured_llm.invoke(messages)
    llm_calls += 1

    return critique, llm_calls, total_tokens


# ── LangGraph nodes ───────────────────────────────────────────────────────────


def run_approval(state: InvoiceState) -> dict[str, Any]:
    """LangGraph node: VP reasoning pass (first pass or post-critique pass).

    On the first pass (review_count == 0):
      - For HIGH_VALUE invoices: sets approval_reasoning, does NOT set
        final_decision yet (the graph will route to critique).
      - For normal invoices: sets final_decision immediately.

    On subsequent passes (review_count > 0):
      - Always sets final_decision (this is the binding post-critique call).
    """
    extracted: dict = state.get("extracted_data", {})
    amount: float = extracted.get("amount") or 0.0
    review_count: int = state.get("review_count", 0)
    critique_notes: str | None = (
        state.get("critique_notes") if review_count > 0 else None
    )

    # Foreign-currency invoices cannot be approved/rejected without an FX rate.
    # Short-circuit to needs_review before calling the LLM.
    flags: list = state.get("validation_flags", [])
    is_foreign_currency = any(f.get("issue_type") == "foreign_currency" for f in flags)
    if is_foreign_currency:
        currency = extracted.get("currency", "non-USD")
        logger.info(
            "Approval: foreign currency (%s) — routing to needs_review", currency
        )
        return {
            "final_decision": "needs_review",
            "decision_reasoning": (
                f"Invoice is denominated in {currency}. "
                "Price and budget validation cannot be completed without an FX rate. "
                "Stock checks passed. Routed to manual review for finance team decision."
            ),
            "approval_reasoning": (
                f"Foreign currency ({currency}) detected. "
                "Cannot approve or reject without a validated exchange rate."
            ),
            "llm_calls": state.get("llm_calls", 0),
            "total_tokens": state.get("total_tokens", 0),
        }

    is_high_value = amount > HIGH_VALUE_THRESHOLD
    has_error_flags = any(f.get("severity") == "error" for f in flags)

    logger.info(
        "Approval (pass %d): amount=%.2f, high_value=%s, error_flags=%s",
        review_count + 1,
        amount,
        is_high_value,
        has_error_flags,
    )

    # ── Precedent check (first pass only) ────────────────────────────────────
    # If this exact flag-pattern has been decided consistently 3+ times before,
    # skip the LLM entirely and apply the learned decision.
    vendor: str = extracted.get("vendor", "")
    if review_count == 0:
        precedent = lookup_precedent(flags, vendor_name=vendor, amount=amount)
        if precedent:
            p_decision = precedent["decision"]
            p_count = precedent["count"]
            p_pattern = precedent["pattern"]
            logger.info(
                "Precedent applied: pattern=%r -> %s (seen %d times)",
                p_pattern,
                p_decision,
                p_count,
            )
            precedent_reasoning = (
                f"[PRECEDENT: {p_decision.upper()}] Auto-decided based on {p_count} "
                f"consistent prior decisions for flag pattern: {p_pattern}."
            )
            # Precedent approvals finalise immediately.
            # Precedent rejections still escalate to Senior Audit — a rejection
            # should always get a second opinion, even when pattern-matched.
            if p_decision == "rejected":
                return {
                    "approval_reasoning": (
                        f"[PRELIMINARY: {p_decision} | awaiting critique]\n{precedent_reasoning}"
                    ),
                    "final_decision": None,
                    "llm_calls": state.get("llm_calls", 0),
                    "total_tokens": state.get("total_tokens", 0),
                    "audit_log": [
                        {
                            "stage": "review",
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "status": "warning",
                            "note": f"precedent rejection -> Senior Audit (pattern={p_pattern}, n={p_count})",
                        }
                    ],
                }
            return {
                "approval_reasoning": precedent_reasoning,
                "final_decision": p_decision,
                "decision_reasoning": (
                    f"Precedent match: flag pattern '{p_pattern}' has been consistently "
                    f"decided as '{p_decision}' across {p_count} prior invoices. "
                    "Applying without LLM review."
                ),
                "llm_calls": state.get("llm_calls", 0),
                "total_tokens": state.get("total_tokens", 0),
                "audit_log": [
                    {
                        "stage": "review",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "status": "ok",
                        "note": f"precedent applied: {p_decision} (pattern={p_pattern}, n={p_count})",
                    }
                ],
            }

    try:
        decision, llm_calls, total_tokens = _run_approval_pass(state, critique_notes)
    except Exception as exc:
        logger.error("Approval agent failed: %s", exc)
        # Fail safe: reject on error rather than silently approve
        return {
            "approval_reasoning": f"Approval agent error: {exc}",
            "final_decision": "rejected",
            "decision_reasoning": f"Rejected due to approval agent failure: {exc}",
            "llm_calls": state.get("llm_calls", 0) + 1,
            "total_tokens": state.get("total_tokens", 0),
            "audit_log": [
                {
                    "stage": "review",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "note": f"LLM error: {exc}",
                }
            ],
        }

    logger.info(
        "Approval pass %d: decision=%s, confidence=%.2f",
        review_count + 1,
        decision.decision,
        decision.confidence,
    )

    updates: dict[str, Any] = {
        "approval_reasoning": decision.reasoning,
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
    }

    # Escalate to Senior Audit when any of these apply (first pass only):
    #   1. Invoice exceeds HIGH_VALUE_THRESHOLD
    #   2. Any validation flag has severity="error"
    #   3. VP confidence < MIN_APPROVAL_CONFIDENCE
    #   4. VP decided to reject: rejections get a second opinion to catch false negatives
    #   5. VP approved but validation flags are present: catch bad approvals that override flags
    has_low_confidence = decision.confidence < MIN_APPROVAL_CONFIDENCE
    has_rejection = decision.decision == "rejected"
    has_active_flags = bool(flags) and decision.decision == "approved"
    needs_scrutiny = (
        is_high_value
        or has_error_flags
        or has_low_confidence
        or has_rejection
        or has_active_flags
    )

    if needs_scrutiny and review_count < MAX_REVIEW_ROUNDS:
        escalation_reason = ", ".join(
            filter(
                None,
                [
                    "high-value" if is_high_value else "",
                    "error flags" if has_error_flags else "",
                    f"low confidence ({decision.confidence:.2f})"
                    if has_low_confidence
                    else "",
                    "rejection review" if has_rejection else "",
                    "approved with flags" if has_active_flags else "",
                ],
            )
        )
        updates["approval_reasoning"] = (
            f"[PRELIMINARY: {decision.decision} | awaiting critique]\n{decision.reasoning}"
        )
        updates["audit_log"] = [
            {
                "stage": "review",
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "warning" if decision.decision == "rejected" else "ok",
                "note": f"preliminary: {decision.decision}, escalated ({escalation_reason})",
            }
        ]
    else:
        # Final binding decision — learn from it for future invoices
        updates["final_decision"] = decision.decision
        updates["decision_reasoning"] = decision.reasoning
        if (
            review_count > 0
            and state.get("critique_notes")
            and decision.decision == "approved"
        ):
            logger.warning(
                "Finalised as APPROVED after critique: %s",
                state.get("critique_notes", "")[:200],
            )
            updates["decision_reasoning"] += (
                "\n\n[NOTE: Critique raised concerns; VP reviewed and finalised approved.]"
            )
        learn_from_decision(flags, decision.decision, vendor_name=vendor)
        updates["audit_log"] = [
            {
                "stage": "review",
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "warning" if decision.decision == "rejected" else "ok",
                "note": f"final: {decision.decision} (confidence={decision.confidence:.2f})",
            }
        ]

    return updates


def run_critique(state: InvoiceState) -> dict[str, Any]:
    """LangGraph node: Senior Auditor reviews the VP's first-pass reasoning.

    Triggered for high-value invoices, error flags, low confidence, rejections,
    or approvals with active validation flags. Never sets the verdict.
    """
    approval_reasoning = state.get("approval_reasoning", "")
    logger.info("Senior Audit pass running")

    try:
        critique, llm_calls, total_tokens = _run_critique_pass(
            state, approval_reasoning
        )
    except Exception as exc:
        logger.error("Critique agent failed: %s", exc)
        return {
            "critique_notes": f"[Critique agent error: {exc}]",
            "review_count": state.get("review_count", 0) + 1,
            "llm_calls": state.get("llm_calls", 0) + 1,
            "total_tokens": state.get("total_tokens", 0),
            "audit_log": [
                {
                    "stage": "review",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "error",
                    "note": f"LLM error: {exc}",
                }
            ],
        }

    concerns_count = len(critique.concerns)
    logger.info("Critique complete: %d concern(s) raised", concerns_count)

    audit_note = (
        f"Senior Audit: {concerns_count} concern(s) raised"
        if concerns_count
        else "Senior Audit: reviewed, no concerns"
    )
    return {
        "critique_notes": critique.critique,
        "review_count": state.get("review_count", 0) + 1,
        "llm_calls": llm_calls,
        "total_tokens": total_tokens,
        "audit_log": [
            {
                "stage": "review",
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "warning" if concerns_count else "ok",
                "note": audit_note,
            }
        ],
    }
