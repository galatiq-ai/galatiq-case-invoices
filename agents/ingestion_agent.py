from pathlib import Path
from typing import Any, Dict

import pdfplumber

from models.invoice_models import Invoice
from tools.llm_tool import LLMTool


class IngestionAgent:
    """
    Reads an invoice file, builds an extraction prompt,
    sends it to the LLM tool, and converts the response into an Invoice model.
    """

    def __init__(self):
        self.llm_tool = LLMTool()

    def read_file(self, invoice_path: str) -> str:
        path = Path(invoice_path)

        if not path.exists():
            raise FileNotFoundError(f"Invoice file not found: {invoice_path}")

        file_extension = path.suffix.lower()

        if file_extension in [".txt", ".json", ".csv", ".xml"]:
            return path.read_text(encoding="utf-8")

        if file_extension == ".pdf":
            return self._read_pdf(path)

        raise ValueError(f"Unsupported file type: {file_extension}")

    def _read_pdf(self, path: Path) -> str:
        text = ""

        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"

        if not text.strip():
            raise ValueError(f"No text could be extracted from PDF: {path}")

        return text

    def _build_extraction_prompt(self, invoice_text: str) -> str:
        return f"""
You are an invoice extraction agent.

Extract structured data from the invoice text below.

Return ONLY valid JSON. Do not include markdown or explanation.

Required JSON format:
{{
  "invoice_number": "string or null",
  "vendor": "string or null",
  "date": "string or null",
  "due_date": "string or null",
  "items": [
    {{
      "name": "string",
      "quantity": integer,
      "unit_price": number or null
    }}
  ],
  "total_amount": number or null,
  "currency": "USD"
}}

Rules:
- Do not guess missing values.
- If a field is missing or unclear, use null.
- Quantity must be an integer.
- Total amount must be a number.
- Verify extracted values against the original invoice text.

Invoice text:
{invoice_text}
"""

    def process(self, invoice_path: str) -> Invoice:
        raw_text = self.read_file(invoice_path)

        prompt = self._build_extraction_prompt(raw_text)

        raw_llm_response = self.llm_tool.call_llm(prompt)

        extracted_data: Dict[str, Any] = self.llm_tool.parse_json_response(raw_llm_response)

        invoice = Invoice(**extracted_data)

        return invoice