from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_pipeline.ingestion.structured import parse_csv, parse_xml

CORPUS = Path(__file__).parent.parent / "data" / "invoices"
D = Decimal


def csv_doc(name: str):
    (result,) = parse_csv((CORPUS / name).read_text(), name)
    return result


def xml_doc(name: str = "invoice_1014.xml"):
    return parse_xml((CORPUS / name).read_text(), name)


def test_csv_field_value_shape_1006():
    result = csv_doc("invoice_1006.csv")
    inv = result.invoice
    assert (inv.vendor, inv.invoice_number) == ("Acme Industrial Supplies", "INV-1006")
    assert inv.invoice_date == date(2026, 1, 25) and inv.due_date_text == "2026-02-10"
    assert [(i.sku, i.quantity, i.unit_price) for i in inv.items] == [
        ("WidgetA", D(5), D("250.00")),
        ("WidgetB", D(3), D("500.00")),
    ]
    assert (inv.subtotal, inv.tax, inv.total) == (D("2750.00"), D("0.00"), D("2750.00"))
    assert (inv.payment_terms, inv.currency, inv.source_format) == ("Net 15", "USD", "csv")
    assert result.findings == [] and inv.source_path == "invoice_1006.csv"


def test_csv_row_per_line_shape_with_footer_rows_1007():
    inv = csv_doc("invoice_1007.csv").invoice
    assert (inv.vendor, inv.invoice_number) == ("MegaWidgets Corp", "INV-1007")
    assert inv.invoice_date == date(2026, 1, 28) and inv.due_date_text == "02/28/2026"
    assert [(i.sku, i.quantity, i.unit_price, i.line_total) for i in inv.items] == [
        ("WidgetA", D(20), D("250.00"), D("5000.00")),
        ("WidgetB", D(15), D("500.00"), D("7500.00")),
        ("GadgetX", D(3), D("750.00"), D("2250.00")),
    ]
    assert (inv.subtotal, inv.tax, inv.total) == (D("14750.00"), D("885.00"), D("15525.00"))


def test_csv_money_is_exact_decimal():
    text = "field,value\ninvoice_number,1\nvendor,A\nitem,W\nquantity,1\n"
    text += "unit_price,10.10\ntotal,10.10\n"
    (result,) = parse_csv(text, "x.csv")
    inv = result.invoice
    assert inv.items[0].unit_price == D("10.10") and inv.total == D("10.10")


ROW_HEADER = "Invoice Number,Vendor,Date,Due Date,Item,Qty,Unit Price,Line Total\n"


def test_csv_blank_number_row_with_line_content_continues_the_invoice_above():
    text = ROW_HEADER + "INV-1,Acme,01/28/2026,02/28/2026,WidgetA,2,10.00,20.00\n"
    text += ",,,,WidgetB,1,5.00,5.00\n,,,,,,Total:,25.00\n"
    (result,) = parse_csv(text, "x.csv")
    inv = result.invoice
    assert (inv.invoice_number, inv.vendor, inv.invoice_date) == (
        "INV-1",
        "Acme",
        date(2026, 1, 28),
    )
    assert [(i.sku, i.line_total) for i in inv.items] == [
        ("WidgetA", D("20.00")),
        ("WidgetB", D("5.00")),
    ]
    assert inv.total == D("25.00")


def test_csv_footer_label_is_read_from_any_column():
    text = ROW_HEADER + "INV-1,Acme,01/28/2026,02/28/2026,WidgetA,2,10.00,20.00\n"
    text += ",Subtotal,,,,,,20.00\n,,,TAX (0%):,,,,0.00\n,,,,,,,20.00\n,Total,,,,,,20.00\n"
    (result,) = parse_csv(text, "x.csv")
    inv = result.invoice
    assert (inv.subtotal, inv.tax, inv.total) == (D("20.00"), D("0.00"), D("20.00"))
    assert len(inv.items) == 1


def test_csv_currency_column_is_mapped():
    text = "Invoice Number,Vendor,Currency,Item,Qty,Unit Price,Line Total\n"
    text += "INV-1,Acme,eur,WidgetA,2,10.00,20.00\n"
    (result,) = parse_csv(text, "x.csv")
    assert result.invoice.currency == "EUR"


def test_csv_two_invoice_numbers_become_two_invoices():
    text = ROW_HEADER + "INV-1,Acme,01/28/2026,02/28/2026,WidgetA,2,10.00,20.00\n"
    text += ",,,,,,Total:,20.00\n"
    text += "INV-2,Globex,01/29/2026,02/28/2026,WidgetB,1,5.00,5.00\n"
    text += "INV-2,Globex,01/29/2026,02/28/2026,GadgetX,1,7.00,7.00\n"
    text += ",,,,,,Total:,12.00\n"
    first, second = parse_csv(text, "x.csv")
    assert (first.invoice.invoice_number, first.invoice.vendor) == ("INV-1", "Acme")
    assert [i.sku for i in first.invoice.items] == ["WidgetA"]
    assert first.invoice.total == D("20.00")
    assert (second.invoice.invoice_number, second.invoice.vendor) == ("INV-2", "Globex")
    assert [i.sku for i in second.invoice.items] == ["WidgetB", "GadgetX"]
    assert second.invoice.total == D("12.00")


def test_xml_nested_structure_and_eur_metadata_1014():
    result = xml_doc()
    inv = result.invoice
    assert (inv.vendor, inv.invoice_number) == ("TechParts International", "INV-1014")
    assert inv.currency == "EUR" and inv.invoice_date == date(2026, 1, 26)
    assert inv.due_date_text == "2026-02-26" and inv.payment_terms == "Net 30"
    assert [(i.sku, i.quantity, i.unit_price) for i in inv.items] == [
        ("WidgetA", D(4), D("225.00")),
        ("WidgetB", D(6), D("475.00")),
    ]
    assert (inv.subtotal, inv.tax, inv.total) == (D("3750.00"), D("375.00"), D("4125.00"))
    assert inv.source_format == "xml" and result.findings == []


def test_malformed_xml_raises_for_the_boundary_to_catch():
    with pytest.raises(ValueError):
        parse_xml("<invoice><header>", "bad.xml")


def test_xml_external_entity_is_not_expanded_or_fetched(tmp_path, no_network):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    payload = f"""<?xml version="1.0"?>
<!DOCTYPE invoice [<!ENTITY xxe SYSTEM "file://{secret}"><!ENTITY net SYSTEM "http://127.0.0.1:9/x">]>
<invoice><header><invoice_number>INV-9</invoice_number><vendor>&xxe;&net;</vendor></header>
<line_items><item><name>WidgetA</name><quantity>1</quantity><unit_price>5.00</unit_price></item></line_items>
</invoice>"""
    try:
        inv = parse_xml(payload, "x.xml").invoice
    except ValueError:
        return  # refusing the document outright is also safe
    assert "TOPSECRET" not in (inv.vendor or "")
