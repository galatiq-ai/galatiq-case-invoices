"""Tests for the ingestion agent — all 5 formats, edge cases, confidence."""

import pytest
from pathlib import Path
from agents.ingest_agent import (
    _extract_json, _extract_csv, _extract_xml, _extract_text,
    _resolve_relative_date, _parse_currency_amount, _compute_confidence,
)
from src.state import LineItem

INVOICES = Path(__file__).resolve().parent.parent / "data" / "invoices"


class TestResolveDates:
    def test_yesterday(self):
        from datetime import date, timedelta
        assert _resolve_relative_date("yesterday") == (date.today() - timedelta(1)).isoformat()

    def test_iso_format(self):
        assert _resolve_relative_date("2026-01-15") == "2026-01-15"

    def test_ocr_o_instead_of_zero(self):
        result = _resolve_relative_date("26-Jan-2O26")
        assert result is not None and "2026" in result

    def test_unparseable_returns_none(self):
        assert _resolve_relative_date("not a date") is None


class TestParseCurrency:
    def test_dollar_sign(self):
        assert _parse_currency_amount("$1,500.00") == 1500.0

    def test_ocr_o_substitution(self):
        assert _parse_currency_amount("3,500.O0") == 3500.0

    def test_plain_number(self):
        assert _parse_currency_amount("250") == 250.0

    def test_negative(self):
        assert _parse_currency_amount("-250.00") == -250.0


class TestExtractJson:
    def test_inv_1004_clean(self):
        text = (INVOICES / "invoice_1004.json").read_text(encoding="utf-8")
        inv = _extract_json(text, "invoice_1004")
        assert inv.invoice_number == "INV-1004"
        assert inv.vendor == "Precision Parts Ltd."
        assert len(inv.items) == 2
        assert inv.total == 1890.0

    def test_inv_1009_negative_quantity_extracted(self):
        text = (INVOICES / "invoice_1009.json").read_text(encoding="utf-8")
        inv = _extract_json(text, "invoice_1009")
        assert any(li.qty < 0 for li in inv.items)

    def test_inv_1016_unknown_item_present(self):
        text = (INVOICES / "invoice_1016.json").read_text(encoding="utf-8")
        inv = _extract_json(text, "invoice_1016")
        assert "WidgetC" in [li.item for li in inv.items]


class TestExtractCsv:
    def test_inv_1006_keyvalue_format(self):
        text = (INVOICES / "invoice_1006.csv").read_text(encoding="utf-8")
        inv = _extract_csv(text)
        assert inv.invoice_number == "INV-1006"
        assert len(inv.items) == 2

    def test_inv_1007_tabular_no_summary_rows(self):
        text = (INVOICES / "invoice_1007.csv").read_text(encoding="utf-8")
        inv = _extract_csv(text)
        item_names_lower = [li.item.lower() for li in inv.items]
        assert "subtotal" not in item_names_lower
        assert "total" not in item_names_lower


class TestExtractXml:
    def test_inv_1014_eur_currency(self):
        text = (INVOICES / "invoice_1014.xml").read_text(encoding="utf-8")
        inv = _extract_xml(text)
        assert inv.currency == "EUR"
        assert len(inv.items) > 0


class TestExtractText:
    def test_inv_1001_clean(self):
        text = (INVOICES / "invoice_1001.txt").read_text(encoding="utf-8")
        inv = _extract_text(text, "invoice_1001")
        assert "INV-1001" in inv.invoice_number.upper()
        assert len(inv.items) == 2

    def test_inv_1008_email_body_extracts_items(self):
        text = (INVOICES / "invoice_1008.txt").read_text(encoding="utf-8")
        inv = _extract_text(text, "invoice_1008")
        assert len(inv.items) > 0  # At least one item found after stripping email headers

    def test_inv_1012_ocr_artifacts_yield_nonzero_total(self):
        text = (INVOICES / "invoice_1012.txt").read_text(encoding="utf-8")
        inv = _extract_text(text, "invoice_1012")
        assert inv.total > 0


class TestConfidenceScore:
    def test_full_data_high_confidence(self):
        score = _compute_confidence(
            "INV-001", "Acme Corp",
            [LineItem(item="WidgetA", qty=5, unit_price=100.0)],
            500.0, "no urgency"
        )
        assert score >= 0.8

    def test_urgency_language_lowers_confidence(self):
        score = _compute_confidence(
            "INV-001", "Vendor",
            [LineItem(item="WidgetA", qty=1)],
            100.0, "PAY IMMEDIATELY to avoid penalties"
        )
        assert score < 0.8

    def test_missing_fields_low_confidence(self):
        score = _compute_confidence("unknown", "Unknown Vendor", [], 0.0, "")
        assert score < 0.5
