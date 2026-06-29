from agents.ingestion_agent import IngestionAgent
from agents.validation_agent import ValidationAgent
from agents.approval_agent import ApprovalAgent

ingestion_agent = IngestionAgent()
validation_agent = ValidationAgent()
approval_agent = ApprovalAgent()

invoice = ingestion_agent.process("data/invoices/invoice_1001.txt")
validation_result = validation_agent.validate(invoice)
approval_result = approval_agent.decide(invoice, validation_result)

print(approval_result.model_dump_json(indent=2))