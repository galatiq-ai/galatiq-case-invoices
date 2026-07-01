"""
Optional utility to generate PDF versions of the sample invoices.

Requires: pip install fpdf2

Usage: python data/generate_pdfs.py
"""

import os
import sys

try:
    from fpdf import FPDF
except ImportError:
    print("fpdf2 is required to generate PDFs: pip install fpdf2")
    sys.exit(1)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "invoices")


def create_clean_invoice():
    """INV-1011: Clean, well-structured invoice."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "INVOICE", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("Helvetica", "", 11)
    for label, value in [
        ("Invoice Number:", "INV-1011"),
        ("Vendor:", "Summit Manufacturing Co."),
        ("Date:", "2026-01-20"),
        ("Due Date:", "2026-02-20"),
    ]:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(45, 7, label)
        pdf.set_font("Helvetica", "", 11)
        pdf.cell(0, 7, value, ln=True)

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 11)
    for header, w in [("Item", 80), ("Qty", 25), ("Unit Price", 35), ("Amount", 35)]:
        align = "C" if header == "Qty" else ("R" if header != "Item" else "L")
        pdf.cell(w, 8, header, border=1, align=align)
    pdf.ln()

    pdf.set_font("Helvetica", "", 11)
    items = [("WidgetA", 6, 250.00), ("WidgetB", 3, 500.00)]
    subtotal = 0
    for item, qty, price in items:
        amount = qty * price
        subtotal += amount
        pdf.cell(80, 7, item, border=1)
        pdf.cell(25, 7, str(qty), border=1, align="C")
        pdf.cell(35, 7, f"${price:,.2f}", border=1, align="R")
        pdf.cell(35, 7, f"${amount:,.2f}", border=1, align="R")
        pdf.ln()

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(140, 7, "Total:", align="R")
    pdf.cell(35, 7, f"${subtotal:,.2f}", align="R", ln=True)

    pdf.output(os.path.join(OUTPUT_DIR, "invoice_1011.pdf"))
    print("  Created invoice_1011.pdf")


def create_messy_invoice():
    """INV-1012: Scanned-style messy invoice with OCR-like artifacts."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Courier", "", 12)

    lines = [
        "                    I N V O I C E",
        "",
        "  FROM:  QuickShip Distributers",
        "         (formerly FastShip Ltd.)",
        "",
        "  INV NO:    INV 1012",
        "  DATE:      26-Jan-2O26",
        "  DUE:       25-Feb-2026",
        "",
        "  TO:    ACME Corp",
        "         Attn: Accounts Payble",
        "",
        "  ----------------------------------------",
        "  ITEM          QTY    PRICE     TOTAL",
        "  ----------------------------------------",
        "  Widget A       12    $250     $3,000.00",
        "  WidgetB         7    $500     $3,500.O0",
        "  Gadget X        4    $750     $3,000.00",
        "  ----------------------------------------",
        "                  SUBTOTAL:     $9,500.00",
        "                  TAX (5%):       $475.00",
        "                  TOTAL:        $9,975.00",
        "",
        "  NOTES: Ref PO-20260115. Deliver to",
        "         warehouse dock B. Contact Jim",
        "         at ext 4421 with questions.",
        "",
        "  Terms: Net 30",
    ]

    for line in lines:
        pdf.cell(0, 6, line, ln=True)

    pdf.output(os.path.join(OUTPUT_DIR, "invoice_1012.pdf"))
    print("  Created invoice_1012.pdf")


def create_bulk_invoice():
    """INV-1013: Large multi-line-item invoice."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Bulk Order Invoice", ln=True, align="C")
    pdf.ln(3)

    pdf.set_font("Helvetica", "", 10)
    for line in [
        "Invoice: INV-1013                              Date: 2026-01-24",
        "Vendor: Atlas Industrial Supply                 Due:  2026-03-24",
        "Terms: Net 60",
    ]:
        pdf.cell(0, 6, line, ln=True)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 10)
    for header, w in [
        ("Item", 60),
        ("Qty", 20),
        ("Unit Price", 30),
        ("Amount", 30),
        ("Notes", 40),
    ]:
        pdf.cell(w, 7, header, border=1)
    pdf.ln()

    pdf.set_font("Helvetica", "", 10)
    bulk_items = [
        ("WidgetA", 15, 250.00, ""),
        ("WidgetB", 10, 500.00, ""),
        ("GadgetX", 5, 750.00, ""),
        ("WidgetA", 5, 240.00, "Volume discount"),
        ("WidgetB", 8, 480.00, "Volume discount"),
        ("GadgetX", 3, 750.00, "Expedited"),
        ("WidgetA", 2, 250.00, "Replacement"),
        ("GadgetX", 1, 750.00, "Sample"),
    ]

    running_total = 0
    for item, qty, price, note in bulk_items:
        amount = qty * price
        running_total += amount
        pdf.cell(60, 6, item, border=1)
        pdf.cell(20, 6, str(qty), border=1, align="C")
        pdf.cell(30, 6, f"${price:,.2f}", border=1, align="R")
        pdf.cell(30, 6, f"${amount:,.2f}", border=1, align="R")
        pdf.cell(40, 6, note, border=1)
        pdf.ln()

    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 10)
    tax = running_total * 0.07
    grand_total = running_total + tax
    pdf.cell(110, 7, "Subtotal:", align="R")
    pdf.cell(30, 7, f"${running_total:,.2f}", align="R", ln=True)
    pdf.cell(110, 7, "Tax (7%):", align="R")
    pdf.cell(30, 7, f"${tax:,.2f}", align="R", ln=True)
    pdf.cell(110, 7, "Grand Total:", align="R")
    pdf.cell(30, 7, f"${grand_total + 50:,.2f}", align="R", ln=True)

    pdf.output(os.path.join(OUTPUT_DIR, "invoice_1013.pdf"))
    print("  Created invoice_1013.pdf")


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print("Generating PDF invoices...")
    create_clean_invoice()
    create_messy_invoice()
    create_bulk_invoice()
    print("Done.")
