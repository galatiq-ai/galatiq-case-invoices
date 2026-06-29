from models.invoice_models import PaymentResult

def mock_payment(vendor: str, amount: float) -> PaymentResult:
    print(f"Paid ${amount:.2f} to {vendor}")
    return PaymentResult(
        status="success",
        vendor=vendor,
        amount=amount
    )