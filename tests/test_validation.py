from agents.ingestion_agent import IngestionAgent
from agents.validation_agent import ValidationAgent

ingestion_agent = IngestionAgent()
validation_agent = ValidationAgent()

invoice = ingestion_agent.process(
    "data/invoices/invoice_1009.json"
)

validation_result = validation_agent.validate(invoice)

print(validation_result.model_dump_json(indent=2))