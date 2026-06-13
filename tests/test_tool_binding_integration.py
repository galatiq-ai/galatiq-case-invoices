import json

import src.config as config


def test_ingest_uses_structured_binding(monkeypatch):
    class FakeLLM:
        def invoke(self, messages, **kwargs):
            # If a schema-like kwarg is passed, return a parsed dict
            if any(k in kwargs for k in ("response_schema", "schema", "response_format", "functions")):
                return {
                    "invoice_number": "INV-FAKE",
                    "vendor": "Widgets Inc.",
                    "items": [],
                    "total": 123.45,
                    "currency": "USD",
                    "confidence": 0.98,
                }
            # Otherwise return JSON text
            return json.dumps({"invoice_number": "INV-RAW"})

    # Force get_llm() to return our fake LLM
    monkeypatch.setattr(config, "get_llm", lambda: FakeLLM())

    import src.llm_binding as llm_binding
    # Ensure the binding helper uses our fake response path
    def fake_invoke_model_with_schema(llm, messages, schema=None, timeout=None):
        return FakeLLM().invoke(messages, schema=schema)

    monkeypatch.setattr(llm_binding, "invoke_model_with_schema", fake_invoke_model_with_schema)

    # Call the ingestion extraction helper directly
    from agents.ingest_agent import _llm_extract

    extracted = _llm_extract("Some invoice text here", "data/invoices/invoice_1001.txt")
    assert extracted.invoice_number == "INV-FAKE"
    assert extracted.vendor == "Widgets Inc."


def test_approval_uses_structured_binding(monkeypatch):
    class FakeLLM:
        def invoke(self, messages, **kwargs):
            if any(k in kwargs for k in ("response_schema", "schema", "response_format", "functions")):
                return {"decision": "approved", "reason": "All good", "required_actions": [], "correction_hints": []}
            return json.dumps({"decision": "approved", "reason": "All good"})

    monkeypatch.setattr(config, "get_llm", lambda: FakeLLM())

    import src.llm_binding as llm_binding
    monkeypatch.setattr(llm_binding, "invoke_model_with_schema", lambda llm, messages, schema=None, timeout=None: {"decision": "approved", "reason": "All good", "required_actions": [], "correction_hints": []})

    from agents.approval_agent import approve_node

    state = {
        "extracted_invoice": {"invoice_number": "INV-APPROVE", "vendor": "Widgets Inc.", "total": 50.0, "currency": "USD", "items": []},
        "validation_result": None,
    }

    out = approve_node(state)
    assert out["approval_decision"]["decision"] == "approved"
