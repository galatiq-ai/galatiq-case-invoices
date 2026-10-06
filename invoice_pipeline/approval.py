"""The Rule Engine: one Decision per arrival, from the Case File and injected model roles."""

from decimal import Decimal

from invoice_pipeline.model import (
    Agents,
    CaseFile,
    Decision,
    Finding,
    FindingCode,
    Outcome,
    Severity,
)
from invoice_pipeline.validation import PRICE_TOLERANCE

HEIGHTENED_SCRUTINY_USD = Decimal("10000")  # LLM Critic approval limit; "above" is strictly greater
CRITIC_PRICE_CEILING = 2 * PRICE_TOLERANCE  # row 5: a larger PRICE_DEVIATION never reaches agents


def _of(case_file: CaseFile, severity: Severity) -> list[Finding]:
    return [f for f in case_file.findings if f.severity is severity]


def _where(finding: Finding) -> str:
    return f"{finding.code}" + (f" line {finding.line}" if finding.line is not None else "")


def check_bounds(case_file: CaseFile) -> list[str]:
    """One failure per Warning outside its Critic Bound (pure; before any model call)."""
    failures = []
    for f in _of(case_file, Severity.WARNING):
        if f.code == FindingCode.VENDOR_UNKNOWN:
            failures.append(f"{_where(f)}: the LLM Critic may never clear it (bound: never)")
        elif f.code == FindingCode.PRICE_DEVIATION:
            deviation = case_file.references.price_deviations.get(f.line)
            if deviation is not None and deviation > CRITIC_PRICE_CEILING:
                failures.append(
                    f"{_where(f)}: deviation {deviation * 100:.2f}% exceeds the "
                    f"{CRITIC_PRICE_CEILING * 100:.0f}% Critic Bound"
                )
    return failures


def _decision(outcome, reasons, row, decided_by="rule_engine", **extra) -> Decision:
    return Decision(
        outcome=outcome, reasons=reasons, precedence_row=row, decided_by=decided_by, **extra
    )


def _reasons(findings: list[Finding]) -> list[str]:
    ordered = sorted(findings, key=lambda f: list(Severity).index(f.severity))
    return [f"{_where(f)}: {f.detail}" for f in ordered]


def _duplicate(case_file: CaseFile) -> Decision:
    arrival, currency = case_file.arrival, case_file.invoice.currency
    claimed = (
        f"already paid {arrival.paid_to_date:.2f} {currency}"
        if arrival.claimed_state == "paid"
        else f"payment of {arrival.paid_to_date:.2f} {currency} pending"
    )
    return _decision(
        Outcome.DUPLICATE,
        [f"DUPLICATE_PAYMENT: {claimed} on arrival #{arrival.duplicate_of}"],
        1,
        duplicate_of=arrival.duplicate_of,
    )


def decide_unreadable(findings: list[Finding]) -> Decision:
    """An Unreadable Document has no invoice: row 3, with no Validation and no model call."""
    return _decision(Outcome.NEEDS_REVIEW, _reasons(findings), 3)


def decide(case_file: CaseFile, agents: Agents) -> Decision:
    """Normative precedence, first matching row wins. Only this function builds a Decision."""
    if case_file.arrival.kind == "duplicate":  # row 1: no model call of any kind
        return _duplicate(case_file)
    if _of(case_file, Severity.REJECTION_RULE):  # row 2
        decision = _decision(Outcome.REJECTED, _reasons(case_file.findings), 2)
    elif _of(case_file, Severity.REVIEW_TRIGGER):  # row 3: human-only
        decision = _decision(Outcome.NEEDS_REVIEW, _reasons(case_file.findings), 3)
    elif warnings := _of(case_file, Severity.WARNING):  # rows 4-5
        names = ", ".join(_where(f) for f in warnings)
        usd = case_file.references.usd_equivalent
        amount = usd.amount if usd is not None else case_file.invoice.total
        if amount is not None and amount > HEIGHTENED_SCRUTINY_USD:
            currency = "USD" if usd is not None else case_file.invoice.currency
            decision = _decision(
                Outcome.NEEDS_REVIEW,
                [
                    f"HEIGHTENED_SCRUTINY: {amount:.2f} {currency} is above the "
                    f"${HEIGHTENED_SCRUTINY_USD:,.0f} Critic approval limit ({names})"
                ],
                4,
            )
        elif failures := check_bounds(case_file):
            decision = _decision(Outcome.NEEDS_REVIEW, failures, 5, bound_failures=failures)
        else:
            # Slice 1 has no online roles: within-bound Warnings never get a usable Critic answer.
            return _decision(
                Outcome.NEEDS_REVIEW,
                [f"UNREVIEWED_WARNINGS: no usable Critic answer for {names}"],
                5,
                unreviewed_warnings=True,
            )
    else:  # row 6: Approved unless the escalate-only review escalates; never adds an approval
        call = agents.escalate_review(case_file)
        answer = call.answer or {}
        if call.tier == "offline" or answer.get("verdict") == "concur":
            return _decision(Outcome.APPROVED, ["no findings"], 6, escalate_review=call)
        if answer.get("verdict") == "escalate":
            reason = f"ESCALATE_ONLY_REVIEW: {answer.get('rationale', 'escalated')}"
            return _decision(
                Outcome.NEEDS_REVIEW, [reason], 6, decided_by="llm_critic", escalate_review=call
            )
        reason = f"ESCALATE_ONLY_REVIEW_FAILED: {call.error or 'no usable answer'}"
        return _decision(Outcome.NEEDS_REVIEW, [reason], 6, escalate_review=call)
    decision.advisory = agents.advise(case_file, decision)  # explains; never read back
    return decision
