from agents.ingestion_agent import IngestionAgent

agent = IngestionAgent()

invoice = agent.process("data/invoices/invoice_1001.txt")

print(invoice.model_dump_json(indent=2))