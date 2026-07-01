"""Unit tests for the ingestion agent.

Uses mocked LLM responses — never calls the real API.
"""

import os
import sys
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.agents.ingestion_agent import (
    _normalise_item_names,
    _route_parser,
    run_ingestion,
)
from src.graph.state import ExtractedData, LineItem


# ── Parser routing ────────────────────────────────────────────────────────────


def test_route_parser_txt(tmp_path):
    f = tmp_path / "inv.txt"
    f.write_text("INVOICE\nVendor: Test Corp\n")
    result = _route_parser(str(f))
    assert "Test Corp" in result


def test_route_parser_json(tmp_path):
    f = tmp_path / "inv.json"
    f.write_text('{"invoice_number": "INV-001", "vendor": {"name": "Acme"}}')
    result = _route_parser(str(f))
    assert "INV-001" in result
    assert "Acme" in result


def test_route_parser_csv(tmp_path):
    f = tmp_path / "inv.csv"
    f.write_text(
        "Invoice Number,Vendor,Date,Due Date,Item,Qty,Unit Price,Line Total\nINV-002,BigCo,2026-01-01,2026-02-01,WidgetA,5,250.00,1250.00\n"
    )
    result = _route_parser(str(f))
    assert "BigCo" in result or "INV-002" in result


def test_route_parser_xml(tmp_path):
    f = tmp_path / "inv.xml"
    f.write_text("""<?xml version="1.0"?><invoice>
      <header><invoice_number>INV-003</invoice_number><vendor>TechCo</vendor>
      <currency>EUR</currency></header></invoice>""")
    result = _route_parser(str(f))
    assert "TechCo" in result
    assert "EUR" in result


def test_route_parser_empty_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    result = _route_parser(str(f))
    assert result == ""


def test_route_parser_missing_file():
    result = _route_parser("/nonexistent/path/invoice.txt")
    assert result == ""


# ── Item normalisation ────────────────────────────────────────────────────────


def test_normalise_widget_a_with_space():
    data = ExtractedData(items=[LineItem(name="Widget A", qty=5)])
    result = _normalise_item_names(data)
    assert result.items[0].name == "WidgetA"


def test_normalise_gadget_x():
    data = ExtractedData(items=[LineItem(name="Gadget X", qty=2)])
    result = _normalise_item_names(data)
    assert result.items[0].name == "GadgetX"


def test_normalise_unknown_item_unchanged():
    data = ExtractedData(items=[LineItem(name="SuperGizmo", qty=3)])
    result = _normalise_item_names(data)
    assert result.items[0].name == "SuperGizmo"


# ── run_ingestion with mocked LLM ─────────────────────────────────────────────


@patch("src.agents.ingestion_agent.get_llm")
def test_ingestion_clean_invoice(mock_get_llm, tmp_path):
    """Ingestion returns extracted data for a clean text invoice."""
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm

    # with_structured_output returns a mock that on invoke() returns ExtractedData
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.return_value = ExtractedData(
        invoice_number="INV-1001",
        vendor="Widgets Inc.",
        amount=5000.00,
        currency="USD",
        items=[
            LineItem(name="WidgetA", qty=10, unit_price=250.00),
            LineItem(name="WidgetB", qty=5, unit_price=500.00),
        ],
        due_date="2026-02-01",
        extraction_confidence=0.95,
    )

    f = tmp_path / "invoice_1001.txt"
    f.write_text(
        "INVOICE\nVendor: Widgets Inc.\nInvoice: INV-1001\n"
        "WidgetA qty: 10\nWidgetB qty: 5\nTotal: $5,000.00\n"
    )

    state = run_ingestion({"invoice_path": str(f)})
    ed = state["extracted_data"]
    assert ed["vendor"] == "Widgets Inc."
    assert ed["amount"] == 5000.00
    assert len(ed["items"]) == 2
    assert state["llm_calls"] == 1


@patch("src.agents.ingestion_agent.get_llm")
def test_ingestion_unreadable_file_no_crash(mock_get_llm):
    """Ingestion produces a degraded state for an unreadable file."""
    state = run_ingestion({"invoice_path": "/totally/missing/file.txt"})
    ed = state["extracted_data"]
    assert ed["extraction_warnings"]
    assert "unreadable" in ed["extraction_warnings"][0].lower()


@patch("src.agents.ingestion_agent.get_llm")
def test_ingestion_llm_failure_no_crash(mock_get_llm, tmp_path):
    """Ingestion degrades gracefully if the LLM call raises an exception."""
    mock_llm = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.side_effect = RuntimeError("LLM unavailable")

    f = tmp_path / "inv.txt"
    f.write_text("INVOICE\nVendor: Test")

    state = run_ingestion({"invoice_path": str(f)})
    ed = state["extracted_data"]
    assert ed["extraction_confidence"] == 0.0
    assert any("LLM extraction error" in w for w in ed["extraction_warnings"])
