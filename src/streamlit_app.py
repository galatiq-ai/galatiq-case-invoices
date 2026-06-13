#!/usr/bin/env python3
"""Simple Streamlit UI for the invoice pipeline.

Run: streamlit run streamlit_app.py

This app reuses `run_pipeline` from `graph.py` to process a chosen invoice
and displays the extracted invoice, validation, approval and payment results.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
import json

import streamlit as st

from .graph import run_pipeline
from .ui import summarize_result


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "invoices"


def list_invoice_files() -> list[Path]:
    if not DATA_DIR.exists():
        return []
    exts = {".txt", ".json", ".csv", ".xml", ".pdf"}
    return sorted([p for p in DATA_DIR.iterdir() if p.suffix.lower() in exts])


def main() -> None:
    st.set_page_config(page_title="Galatiq Invoice UI", layout="wide")
    st.title("Galatiq — Invoice Processing UI")

    with st.sidebar:
        st.header("Input")
        builtins = list_invoice_files()
        builtin_names = [p.name for p in builtins]
        selection = st.selectbox("Choose a sample invoice", ["-- upload or choose --"] + builtin_names)
        uploaded = st.file_uploader("Or upload an invoice file", type=["txt", "json", "csv", "xml", "pdf"])
        # approver = st.text_input("Approver username", value="")
        run_btn = st.button("Process invoice")

    file_path = None
    if uploaded is not None:
        # Save uploaded file to a temp file and process that path
        suffix = Path(uploaded.name).suffix or ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded.read())
            file_path = tmp.name

    elif selection and selection != "-- upload or choose --":
        file_path = str(DATA_DIR / selection)

    if run_btn and not file_path:
        st.warning("Please upload a file or select a sample invoice from the sidebar.")

    if run_btn and file_path:
        st.info(f"Processing: {file_path}")
        with st.spinner("Running pipeline..."):
            try:
                result = run_pipeline(file_path)
            except Exception as exc:
                st.error(f"Pipeline error: {exc}")
                return

        # Top-level status and UI summary
        if result.get("error"):
            st.error(f"Error: {result.get('error')}")

        summary = summarize_result(result)
        header = summary["header"]
        cards = summary["cards"]
        timeline = summary["timeline"]
        explanation = summary["explanation"]
        raw = summary["raw"]

        # Header area
        hcol1, hcol2, hcol3 = st.columns([2, 2, 1])
        with hcol1:
            st.markdown(f"**Invoice:** {header.get('invoice_number', 'N/A')}")
            st.markdown(f"**Vendor:** {header.get('vendor', 'N/A')}")
        with hcol2:
            total = header.get('total')
            currency = header.get('currency', 'USD')
            if total is not None:
                st.metric(label="Total", value=f"{total:,.2f} {currency}")
            else:
                st.write("\n")
        with hcol3:
            status = header.get('status')
            if status == 'paid' or status == 'approved':
                st.success(status.upper())
            elif status == 'hold':
                st.warning('HOLD')
            elif status == 'rejected':
                st.error('REJECTED')
            elif status == 'error':
                st.error('ERROR')
            else:
                st.info(status.upper())

        # Outcome cards
        st.markdown("---")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.subheader("Extraction")
            if cards['extraction']['present']:
                st.success("Invoice extracted")
            else:
                st.error("No invoice extracted")
        with c2:
            st.subheader("Validation")
            if cards['validation']['passed']:
                st.success("Passed")
            else:
                st.warning(cards['validation'].get('summary') or "Issues found")
        with c3:
            st.subheader("Approval")
            dec = cards['approval'].get('decision')
            if dec == 'approved':
                st.success('Approved')
            elif dec == 'hold':
                st.warning('On hold')
            elif dec == 'rejected':
                st.error('Rejected')
            else:
                st.info('No decision')
        with c4:
            st.subheader("Payment")
            pay = cards['payment']
            if pay.get('status') == 'success':
                st.success('Paid')
            elif pay.get('status') == 'failed':
                st.error('Failed')
            else:
                st.info('Not processed')

        # Timeline
        st.markdown("---")
        st.subheader("Processing Timeline")
        tl_rows = [(t['step'], t['status']) for t in timeline]
        st.table(tl_rows)

        # Plain-language explanation
        st.markdown("---")
        st.subheader("What happened / Next actions")
        for line in explanation:
            st.write(line)

        # Collapsible technical details
        st.markdown("---")
        with st.expander("Show technical details"):
            st.subheader("Extracted Invoice")
            st.json(raw.get('extracted_invoice') or {})
            st.subheader("Validation Result")
            st.json(raw.get('validation_result') or {})
            st.subheader("Approval Decision")
            st.json(raw.get('approval_decision') or {})
            st.subheader("Payment Result")
            st.json(raw.get('payment_result') or {})
            st.subheader("Trace Log")
            trace = raw.get('trace_log') or []
            if trace:
                for entry in trace:
                    st.write(entry)
            else:
                st.write("No trace available.")

        st.sidebar.success("Done")


if __name__ == "__main__":
    main()
