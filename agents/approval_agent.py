import json

from models.invoice_models import Invoice, ValidationResult, ApprovalResult
from tools.llm_tool import LLMTool


class ApprovalAgent:
    """
    Decides whether an invoice should be approved or rejected.
    Validation failures are rejected directly.
    Valid invoices are reviewed by the LLM with a reflection loop.
    """

    def __init__(self):
        self.llm_tool = LLMTool()

    def decide(self, invoice: Invoice, validation_result: ValidationResult) -> ApprovalResult:
        if not validation_result.passed:
            reasons = [issue.message for issue in validation_result.issues]

            return ApprovalResult(
                approved=False,
                status="REJECTED",
                reason="Validation failed: " + "; ".join(reasons),
                reflection="Rejected without LLM review because validation failed."
            )

        decision_prompt = self._build_decision_prompt(invoice)
        raw_decision = self.llm_tool.call_llm(decision_prompt)
        decision = self.llm_tool.parse_json_response(raw_decision)

        reflection_prompt = self._build_reflection_prompt(invoice, decision)
        reflection = self.llm_tool.call_llm(reflection_prompt)

        return ApprovalResult(
            approved=decision["approved"],
            status=decision["status"],
            reason=decision["reason"],
            reflection=reflection
        )

    def _build_decision_prompt(self, invoice: Invoice) -> str:
        return f"""
You are a VP-level invoice approval agent.

Decide whether this invoice should be approved for payment.

Business rules:
- Validation has already passed.
- If invoice amount is <= 10000, approve.
- If invoice amount is > 10000, approve with additional scrutiny.
- Do not reject a valid invoice only because it is above 10000.
- Return ONLY valid JSON. No markdown. No explanation outside JSON.
- The reason field must not be empty.

Required JSON format:
{{
  "approved": true or false,
  "status": "APPROVED" or "REJECTED" or "APPROVED_WITH_SCRUTINY",
  "reason": "required non-empty short explanation"
}}

Invoice:
{invoice.model_dump_json(indent=2)}
"""

    def _build_reflection_prompt(self, invoice: Invoice, decision: dict) -> str:
        return f"""
You are reviewing a previous invoice approval decision.

Original invoice:
{invoice.model_dump_json(indent=2)}

Initial decision:
{json.dumps(decision, indent=2)}

Review whether the decision follows these business rules:
- Valid invoices <= 10000 should be approved.
- Valid invoices > 10000 may be approved with additional scrutiny.
- The decision should not invent facts.
Return one concise sentence confirming whether the decision follows the rules. Do not critique wording style.

"""