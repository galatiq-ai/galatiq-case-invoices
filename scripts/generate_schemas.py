"""Generate JSON schema files from Pydantic models. Run once.

Usage:
    python scripts/generate_schemas.py
"""

import json, os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from src.state import ExtractedInvoice, ValidationResult, ApprovalDecision

schemas_dir = os.path.join(os.path.dirname(__file__), "..", "schemas")
os.makedirs(schemas_dir, exist_ok=True)

for filename, model in [
    ("invoice_schema.json", ExtractedInvoice),
    ("validation_result.json", ValidationResult),
    ("approval_decision.json", ApprovalDecision),
]:
    path = os.path.join(schemas_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(model.model_json_schema(), f, indent=2)
    print(f"Generated {filename}")
