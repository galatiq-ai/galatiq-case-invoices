"""Unit tests for UI helper `summarize_result`."""

from src.ui import summarize_result


def test_summarize_paid_invoice():
    result = {
        "file_path": "data/invoices/invoice_1001.txt",
        "extracted_invoice": {"invoice_number": "1001", "vendor": "Acme", "total": 123.45, "currency": "USD"},
        "validation_result": {"passed": True, "summary": "All good"},
        "approval_decision": {"decision": "approved", "reason": "Auto-approved"},
        "payment_result": {"status": "success", "tx_id": "TX-123"},
        "trace_log": ["ingest ok", "validate ok", "approve ok", "payment ok"],
    }

    s = summarize_result(result)
    assert s["header"]["invoice_number"] == "1001"
    assert s["header"]["status"] == "paid"
    assert s["cards"]["validation"]["passed"] is True


def test_summarize_hold_invoice_with_hints():
    result = {
        "file_path": "data/invoices/invoice_2000.txt",
        "extracted_invoice": {"invoice_number": "2000", "vendor": "Beta", "total": 50},
        "validation_result": {"passed": False, "summary": "Quantity mismatch"},
        "approval_decision": {"decision": "hold", "correction_hints": ["check quantity", "confirm SKU"]},
        "payment_result": None,
        "trace_log": [],
    }

    s = summarize_result(result)
    assert s["header"]["status"] == "hold"
    assert any("check quantity" in x for x in s["explanation"]) or any("requires human" in x for x in s["explanation"]) is False


def test_summarize_error_state():
    result = {"file_path": "data/invoices/missing.txt", "error": "File not found"}
    s = summarize_result(result)
    assert s["header"]["status"] == "error"
    assert any("Processing error" in x for x in s["explanation"]) is True
