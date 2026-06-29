from agents.ingestion_agent import IngestionAgent
from agents.validation_agent import ValidationAgent
from agents.approval_agent import ApprovalAgent
from agents.payment_agent import PaymentAgent

ingestion_agent = IngestionAgent()
validation_agent = ValidationAgent()
approval_agent = ApprovalAgent()
payment_agent = PaymentAgent()

invoice = ingestion_agent.process("data/invoices/invoice_1001.txt")

validation_result = validation_agent.validate(invoice)
approval_result = approval_agent.decide(invoice, validation_result)
payment_result = payment_agent.process_payment(
    invoice,
    approval_result
)

print("\n========== VALIDATION ==========")
print(validation_result.model_dump_json(indent=2))

print("\n========== APPROVAL ==========")
print(approval_result.model_dump_json(indent=2))

print("\n========== PAYMENT ==========")
print(payment_result)