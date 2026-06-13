# LLM Prompt Templates

These prompts are used by the ingestion and approval agents when calling
the configured LLM. When no API key is available, the system falls back
to deterministic extraction (the `_deterministic_extract` path).

## Ingestion Prompt

Extract structured invoice data from the raw text below.
Return a JSON object with these fields:
- invoice_number: string
- vendor: string (company name)
- date: string (YYYY-MM-DD, or null if not found)
- due_date: string (YYYY-MM-DD, or null if not found)
- items: array of {item: string, qty: int, unit_price: float | null}
- total: float
- currency: string (default "USD")

Rules:
- Resolve relative dates like "yesterday" to absolute dates.
- Fix obvious OCR artifacts (e.g., "2O26" → "2026", "3,500.O0" → "3500.00").
- Normalize item names (e.g., "Widget A" → "WidgetA", "Gadget X" → "GadgetX").
- If a vendor name is empty or missing, set it to "Unknown Vendor".
- For negative quantities, keep them as-is (they will be caught by validation).
- Return raw text exactly as provided in the `raw_text` field.
- Output valid JSON only, no markdown or commentary.

Confidence scoring:
After extraction, rate confidence 0.0–1.0 based on:
- All required fields present? (-0.2 each missing)
- Has vendor name? (+0.3)
- Has at least one line item? (+0.3)
- Total > 0? (+0.2)
- Invoice number looks valid? (+0.2)
- Items have both name and quantity? (+0.3)
- No suspicious urgency language? (+0.2)

Base confidence starts at 0.5.

## Approval Critique Prompt

You are a VP-level approver reviewing an invoice. Here is the extracted data:

Invoice: {invoice_number}
Vendor: {vendor}
Total: {total} {currency}
Items: {items}
Validation Results: {validation_summary}

Consider:
1. Is the total over $10,000? If so, apply additional scrutiny.
2. Are there any validation flags? Stock mismatches, unknown items, data integrity issues?
3. Is the vendor known and reputable?
4. Does the language in the invoice indicate urgency pressure ("pay immediately", "avoid penalties")?
5. Is this a revised invoice? (same number already processed)

Make a decision: "approved", "rejected", or "hold".
Provide clear reasoning.
Also return a `correction_hints` array with short, actionable guidance for the next extraction pass when the invoice should be re-read.

Critique loop: Re-read your decision. Would the VP's VP agree?
If there is any doubt, revise toward "hold" with required actions.
