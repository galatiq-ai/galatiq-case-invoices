from models.invoice_models import Invoice, ApprovalResult, PaymentResult
from tools.payment_tool import mock_payment


class PaymentAgent:
    """
    Processes payment only if the invoice is approved.
    """

    def process_payment(
        self,
        invoice: Invoice,
        approval_result: ApprovalResult
    ) -> PaymentResult:
        if not approval_result.approved:
            return PaymentResult(
                status="not_paid",
                reason=approval_result.reason
            )

        return mock_payment(
            vendor=invoice.vendor or "Unknown Vendor",
            amount=invoice.total_amount or 0.0
        )