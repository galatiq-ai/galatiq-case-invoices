"""Streamlit views for each pipeline stage in InvoiceAI."""

from __future__ import annotations

from html import escape as _esc

import pandas as pd
import streamlit as st

from src.db.queries import get_all_invoice_runs
from ui.cards import app_logo, invoice_banner, issue_card, kpi_card
from ui.labels import AUDIT_GLYPHS, AUDIT_PALETTE, OUTCOME_COLORS, verdict_label


# ── Shared primitives ──────────────────────────────────────────────────────────


def stage_divider(title: str) -> None:
    """Labelled horizontal rule used to separate pipeline stages."""
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;margin:20px 0 8px;'>"
        f"<div style='width:9px;height:9px;border-radius:50%;background:#2563EB;"
        f"flex-shrink:0;'></div>"
        f"<span style='font-size:0.76rem;font-weight:700;color:#374151;"
        f"letter-spacing:0.07em;text-transform:uppercase;'>{title}</span>"
        f"<div style='flex:1;height:1px;background:#E5E7EB;'></div>"
        f"</div>",
        unsafe_allow_html=True,
    )


def prose(text: str) -> None:
    """Render LLM-generated text with consistent typography."""
    safe = (
        _esc(text)
        .replace("\n\n", "</p><p style='margin:8px 0 0;'>")
        .replace("\n", "<br>")
    )
    st.markdown(
        "<div style='font-size:0.875rem;font-family:inherit;color:#374151;"
        "line-height:1.72;background:#F8FAFC;padding:14px 18px;"
        "border-radius:8px;border-left:3px solid #DBEAFE;'>"
        f"<p style='margin:0;'>{safe}</p></div>",
        unsafe_allow_html=True,
    )


# ── Landing ────────────────────────────────────────────────────────────────────


def splash() -> None:
    """Welcome screen shown before the user starts processing."""
    st.markdown("<br><br>", unsafe_allow_html=True)
    _, center, _ = st.columns([1, 2.2, 1])
    with center:
        st.markdown(
            f"<div style='text-align:center;margin-bottom:4px;'>{app_logo(32, 12)}</div>"
            "<h1 style='text-align:center;font-size:2.6rem;font-weight:700;"
            "letter-spacing:-0.5px;margin-top:2px;'>InvoiceAI</h1>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<p style='text-align:center;font-size:1.1rem;color:#6B7280;margin-bottom:2.5rem;'>"
            "Automated invoice processing from extraction to payment</p>",
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        steps = [
            ("Extract", "Pulls vendor, items, and amounts from the invoice file"),
            (
                "Validate",
                "Checks stock levels, prices, and totals against your inventory",
            ),
            ("Review", "VP makes an approval decision based on the findings"),
            ("Senior Audit", "Independent review for high-risk or high-value invoices"),
            ("Payment", "Executes payment or logs the rejection"),
        ]
        cols = st.columns(len(steps))
        for i, (col, (label, desc)) in enumerate(zip(cols, steps)):
            with col:
                st.markdown(
                    f"<div style='text-align:center;padding:0 4px;'>"
                    f"<div style='width:40px;height:40px;border-radius:50%;background:#2563EB;"
                    f"color:#fff;font-weight:700;font-size:1rem;display:flex;align-items:center;"
                    f"justify-content:center;margin:0 auto 10px;'>{i + 1}</div>"
                    f"<div style='font-size:0.95rem;font-weight:600;color:#111827;"
                    f"margin-bottom:6px;'>{label}</div>"
                    f"<div style='font-size:0.78rem;color:#6B7280;line-height:1.4;'>{desc}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        st.markdown("<br><br>", unsafe_allow_html=True)
        _, btn_col, _ = st.columns([1, 2, 1])
        if btn_col.button("Get started", type="primary", width="stretch"):
            st.session_state.started = True
            st.rerun()


# ── Stage views ────────────────────────────────────────────────────────────────


def show_extraction(state: dict) -> None:
    extracted = state.get("extracted_data") or {}
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Invoice details**")
        st.write(
            {
                "Invoice number": extracted.get("invoice_number") or "N/A",
                "Vendor": extracted.get("vendor") or "N/A",
                "Amount": (
                    f"{extracted.get('amount') or 0:,.2f} "
                    f"{extracted.get('currency') or 'USD'}"
                ),
                "Due date": extracted.get("due_date") or "N/A",
            }
        )
    with c2:
        items = extracted.get("items") or []
        if items:
            st.markdown("**Line items**")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Item": i.get("name"),
                            "Qty": i.get("qty"),
                            "Unit price": i.get("unit_price"),
                        }
                        for i in items
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
    warnings = extracted.get("extraction_warnings") or []
    if warnings:
        # Split into meaningful warnings vs low-level parse noise
        meaningful = [
            w for w in warnings if not w.lower().startswith("could not parse")
        ]
        noise = [w for w in warnings if w.lower().startswith("could not parse")]
        if meaningful:
            items_html = "".join(
                f"<li style='margin-bottom:4px;'>{_esc(w)}</li>" for w in meaningful
            )
            st.markdown(
                "<div style='background:#FFFBEB;border-left:3px solid #F59E0B;"
                "border-radius:6px;padding:10px 14px;margin-top:8px;'>"
                "<div style='font-size:0.78rem;font-weight:700;color:#92400E;"
                "margin-bottom:6px;'>Extraction warnings</div>"
                f"<ul style='margin:0;padding-left:16px;font-size:0.8rem;"
                f"color:#78350F;line-height:1.6;'>{items_html}</ul>"
                "</div>",
                unsafe_allow_html=True,
            )
        if noise:
            with st.expander(f"{len(noise)} low-level parsing note(s)", expanded=False):
                for n in noise:
                    st.caption(n)
    st.caption(f"Confidence: {extracted.get('extraction_confidence', 1.0):.0%}")


def show_validation(state: dict) -> None:
    flags = state.get("validation_flags") or []
    if not flags:
        st.success("No issues found. Invoice looks clean.")
        return
    st.markdown("".join(issue_card(f) for f in flags), unsafe_allow_html=True)


def show_vp_decision(state: dict) -> None:
    reasoning = state.get("approval_reasoning") or state.get("decision_reasoning") or ""
    if reasoning.startswith("[PRECEDENT:"):
        st.markdown(
            "<span style='background:#EEF2FF;color:#4338CA;font-size:0.74rem;font-weight:700;"
            "padding:3px 10px;border-radius:12px;letter-spacing:0.04em;'>"
            "AUTO-DECIDED (PRECEDENT)</span>",
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)
    if reasoning.startswith("[PRELIMINARY:") and "\n" in reasoning:
        reasoning = reasoning.split("\n", 1)[1].strip()
    prose(reasoning) if reasoning else st.caption("No reasoning recorded.")


def show_audit_notes(state: dict) -> None:
    notes = state.get("critique_notes") or ""
    if notes:
        prose(notes)
    else:
        st.caption("Not triggered for this invoice.")


def show_payment(state: dict) -> None:
    payment = state.get("payment_result") or {}
    status = payment.get("status")
    if status == "success":
        st.success(
            f"Payment executed: ${payment.get('amount', 0):,.2f} "
            f"to {payment.get('vendor', 'Unknown')}"
        )
    elif status == "rejected":
        st.error("Payment withheld. Rejection logged.")
    elif status == "needs_review":
        st.warning("Invoice held for manual review. No payment issued.")
    else:
        st.info("Payment status unavailable.")


def show_pipeline_trace(audit_log: list[dict]) -> None:
    rows = []
    for entry in audit_log:
        status = entry.get("status", "ok")
        glyph = AUDIT_GLYPHS.get(status, "·")
        color = AUDIT_PALETTE.get(status, "#6B7280")
        ts_raw = entry.get("ts", "")
        ts_short = ts_raw[11:19] if len(ts_raw) >= 19 else ts_raw
        rows.append(
            f"<tr>"
            f"<td style='padding:7px 12px;font-size:0.78rem;color:#6B7280;"
            f"white-space:nowrap;border-bottom:1px solid #F3F4F6;'>{ts_short}</td>"
            f"<td style='padding:7px 12px;font-size:0.78rem;font-weight:600;"
            f"color:#374151;border-bottom:1px solid #F3F4F6;'>{entry.get('stage', '')}</td>"
            f"<td style='padding:7px 12px;font-size:0.78rem;font-weight:700;"
            f"color:{color};border-bottom:1px solid #F3F4F6;'>"
            f"{glyph} {status.capitalize()}</td>"
            f"<td style='padding:7px 12px;font-size:0.78rem;color:#6B7280;"
            f"border-bottom:1px solid #F3F4F6;'>{_esc(entry.get('note', ''))}</td>"
            f"</tr>"
        )
    hdr = (
        "background:#F8FAFC;font-size:0.72rem;font-weight:700;color:#9CA3AF;"
        "text-transform:uppercase;letter-spacing:0.06em;padding:8px 12px;"
        "border-bottom:2px solid #E5E7EB;text-align:left;"
    )
    h_html = "".join(
        f"<th style='{hdr}'>{h}</th>" for h in ["Time (UTC)", "Stage", "Status", "Note"]
    )
    st.markdown(
        "<div style='border:1px solid #E5E7EB;border-radius:8px;overflow:hidden;"
        "box-shadow:0 1px 3px rgba(0,0,0,0.04);'>"
        "<table style='width:100%;border-collapse:collapse;background:#fff;'>"
        f"<thead><tr>{h_html}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )


# ── Full result view ───────────────────────────────────────────────────────────


def show_result(state: dict, elapsed: float | None = None) -> None:
    run_key = state.get("run_id", "default")
    st.markdown(invoice_banner(state), unsafe_allow_html=True)

    if elapsed is not None:
        st.caption(
            f"Completed in {elapsed:.1f}s · "
            f"{state.get('llm_calls', 0)} LLM calls · "
            f"{state.get('total_tokens', 0):,} tokens"
        )

    stage_divider("Extraction")
    _, col = st.columns([0.025, 0.975])
    with col:
        with st.expander("View Extracted Data", expanded=False, key=f"ext_{run_key}"):
            show_extraction(state)

    stage_divider("Validation")
    _, col = st.columns([0.025, 0.975])
    with col:
        show_validation(state)

    stage_divider("VP Review")
    _, col = st.columns([0.025, 0.975])
    with col:
        show_vp_decision(state)

    if state.get("critique_notes"):
        stage_divider("Senior Auditor Review")
        _, col = st.columns([0.025, 0.975])
        with col:
            show_audit_notes(state)

    stage_divider("Payment")
    _, col = st.columns([0.025, 0.975])
    with col:
        show_payment(state)

    audit_log = state.get("audit_log") or []
    if audit_log:
        stage_divider("Pipeline Trace")
        _, col = st.columns([0.025, 0.975])
        with col:
            with st.expander(
                "View audit trail", expanded=False, key=f"trace_{run_key}"
            ):
                show_pipeline_trace(audit_log)


# ── History dashboard ──────────────────────────────────────────────────────────

_SORT_FIELDS = {
    "Date": "CreatedAt",
    "Invoice": "Invoice",
    "Vendor": "Vendor",
    "Amount": "Amount",
}
_STATUS_OPTIONS = ["All", "PAID", "REJECTED", "NEEDS REVIEW"]


def show_history() -> None:
    db_rows = get_all_invoice_runs()

    if not db_rows:
        st.info("No invoices processed yet.")
        return

    # Annotate each row with its display status label
    rows = [{**r, "StatusLabel": verdict_label(r["Status"])} for r in db_rows]

    # ── Filter / sort controls ────────────────────────────────────────────────
    if "hist_filter" not in st.session_state:
        st.session_state.hist_filter = "All"
    if "hist_sort_col" not in st.session_state:
        st.session_state.hist_sort_col = "Date"
    if "hist_sort_asc" not in st.session_state:
        st.session_state.hist_sort_asc = False

    fc, sc, dc = st.columns([2, 2, 1.5])
    with fc:
        st.selectbox("Filter by status", _STATUS_OPTIONS, key="hist_filter")
    with sc:
        st.selectbox("Sort by", list(_SORT_FIELDS.keys()), key="hist_sort_col")
    with dc:
        st.markdown("<div style='margin-top:28px;'></div>", unsafe_allow_html=True)
        st.toggle("Ascending", key="hist_sort_asc")

    # Apply filter
    active_filter = st.session_state.hist_filter
    filtered = (
        rows
        if active_filter == "All"
        else [r for r in rows if r["StatusLabel"] == active_filter]
    )

    # Apply sort
    sort_key = _SORT_FIELDS[st.session_state.hist_sort_col]
    filtered = sorted(
        filtered, key=lambda r: r[sort_key], reverse=not st.session_state.hist_sort_asc
    )

    # ── KPIs: deduplicate by invoice number; fall back to RunId when unknown ──
    unique_count = len(
        {r["Invoice"] if r["Invoice"] != "?" else r["RunId"] for r in filtered}
    )
    paid_total = sum(r["Amount"] for r in filtered if r["StatusLabel"] == "PAID")
    rejected_total = sum(
        r["InvoiceTotal"] for r in filtered if r["StatusLabel"] == "REJECTED"
    )

    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    c1.markdown(
        kpi_card("Invoices Processed", str(unique_count), "#2563EB"),
        unsafe_allow_html=True,
    )
    c2.markdown(
        kpi_card("Total Paid", f"${paid_total:,.2f}", "#16A34A"), unsafe_allow_html=True
    )
    c3.markdown(
        kpi_card("Total Rejected", f"${rejected_total:,.2f}", "#DC2626"),
        unsafe_allow_html=True,
    )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Table ─────────────────────────────────────────────────────────────────
    hdr = (
        "background:#F8FAFC;font-size:0.74rem;font-weight:700;color:#6B7280;"
        "text-transform:uppercase;letter-spacing:0.06em;"
        "padding:12px 16px;text-align:left;border-bottom:2px solid #E5E7EB;"
    )
    cell = "padding:11px 16px;font-size:0.88rem;color:#111827;border-bottom:1px solid #F3F4F6;"
    headers = ["Invoice", "Vendor", "Amount", "Currency", "Status", "Date"]
    h_html = "".join(f"<th style='{hdr}'>{h}</th>" for h in headers)

    body = []
    for r in filtered:
        bg, fg = OUTCOME_COLORS.get(r["StatusLabel"], ("#F3F4F6", "#374151"))
        badge = (
            f"<span style='background:{bg};color:{fg};font-size:0.74rem;font-weight:700;"
            f"padding:4px 12px;border-radius:20px;letter-spacing:0.04em;'>{r['StatusLabel']}</span>"
        )
        inv_label = _esc(r["Invoice"])
        if r.get("IsRevision"):
            inv_label += (
                "<span style='font-size:0.7rem;color:#6B7280;margin-left:6px;"
                "background:#F3F4F6;padding:2px 6px;border-radius:8px;'>revision</span>"
            )
        amount_cell = f"<b>${r['Amount']:,.2f}</b>"
        if r.get("IsRevision"):
            amount_cell += (
                f"<br><span style='font-size:0.72rem;color:#9CA3AF;'>"
                f"invoice total ${r['InvoiceTotal']:,.2f}</span>"
            )
        body.append(
            f"<tr>"
            f"<td style='{cell}'>{inv_label}</td>"
            f"<td style='{cell}'>{_esc(r['Vendor'])}</td>"
            f"<td style='{cell}'>{amount_cell}</td>"
            f"<td style='{cell}'>{_esc(r['Currency'])}</td>"
            f"<td style='{cell}'>{badge}</td>"
            f"<td style='{cell}'><span style='font-size:0.8rem;color:#6B7280;'>"
            f"{_esc(r['CreatedAt'])}</span></td>"
            f"</tr>"
        )

    st.markdown(
        "<div style='border:1px solid #E5E7EB;border-radius:12px;overflow:hidden;"
        "box-shadow:0 1px 4px rgba(0,0,0,0.06);'>"
        "<table style='width:100%;border-collapse:collapse;background:#fff;'>"
        f"<thead><tr>{h_html}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )
