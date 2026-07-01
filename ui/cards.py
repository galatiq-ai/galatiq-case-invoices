"""HTML string builders for InvoiceAI UI elements.

Every function returns a plain string — no Streamlit calls, no side-effects.
Pass the result to ``st.markdown(..., unsafe_allow_html=True)``.
"""

from __future__ import annotations

from html import escape as _esc

from ui.labels import (
    OUTCOME_COLORS,
    PIPELINE_STAGES,
    SEVERITY_THEME,
    issue_label,
    verdict_label,
)

_INVOICE_ICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="white"
     width="{size}" height="{size}">
  <path d="M19.5 3.5L18 2l-1.5 1.5L15 2l-1.5 1.5L12 2l-1.5 1.5L9 2 7.5 3.5 6 2v14H3v3c0
    1.66 1.34 3 3 3h12c1.66 0 3-1.34 3-3V2l-1.5 1.5zM19 19c0 .55-.45 1-1 1H6c-.55
    0-1-.45-1-1v-1h14v1zm1-3H6V4h14v12zM9 7h6v2H9zm0 4h6v2H9z"/>
</svg>
"""


def app_logo(size: int = 28, radius: int = 10) -> str:
    """Blue rounded badge containing the invoice icon."""
    svg = _INVOICE_ICON_SVG.format(size=size)
    return (
        f"<div style='width:{size + 16}px;height:{size + 16}px;background:#2563EB;"
        f"border-radius:{radius}px;display:inline-flex;align-items:center;"
        f"justify-content:center;'>{svg}</div>"
    )


def active_stage(seen: list[str]) -> str | None:
    """Return the next stage that should pulse, skipping any that were bypassed."""
    for i, stage in enumerate(PIPELINE_STAGES):
        if stage in seen:
            continue
        if any(PIPELINE_STAGES.index(s) > i for s in seen):
            continue
        return stage
    return None


def pipeline_tracker(seen: list[str], current: str | None) -> str:
    """Horizontal step indicator showing pipeline progress."""
    parts = []
    for i, stage in enumerate(PIPELINE_STAGES):
        done = stage in seen
        live = (stage == current) and not done

        if done:
            dot = (
                "<div style='width:22px;height:22px;border-radius:50%;background:#16A34A;"
                "display:flex;align-items:center;justify-content:center;flex-shrink:0;'>"
                "<span style='color:white;font-size:13px;font-weight:700;line-height:1;'>"
                "&#10003;</span></div>"
            )
            tc, tw = "#16A34A", "600"
        elif live:
            dot = "<div class='dot-active'></div>"
            tc, tw = "#2563EB", "700"
        else:
            dot = (
                "<div style='width:22px;height:22px;border-radius:50%;"
                "background:#E5E7EB;flex-shrink:0;'></div>"
            )
            tc, tw = "#9CA3AF", "400"

        parts.append(
            f"<div style='display:flex;flex-direction:column;align-items:center;"
            f"gap:6px;min-width:68px;'>"
            f"{dot}"
            f"<span style='font-size:0.7rem;color:{tc};font-weight:{tw};"
            f"text-align:center;white-space:nowrap;'>{stage}</span>"
            f"</div>"
        )
        if i < len(PIPELINE_STAGES) - 1:
            lc = "#16A34A" if done else "#E5E7EB"
            parts.append(
                f"<div style='flex:1;height:2px;background:{lc};"
                f"align-self:center;margin-bottom:20px;min-width:12px;'></div>"
            )

    return (
        "<div style='display:flex;align-items:flex-start;padding:22px 24px 18px;"
        "background:#FFFFFF;border:1px solid #E5E7EB;border-radius:12px;"
        "box-shadow:0 1px 4px rgba(0,0,0,0.06);'>" + "".join(parts) + "</div>"
    )


def invoice_banner(state: dict) -> str:
    """Summary card: vendor, amount, due date, and verdict pill."""
    ext = state.get("extracted_data") or {}
    payment = state.get("payment_result") or {}
    vendor = ext.get("vendor") or "Unknown Vendor"
    inv_num = ext.get("invoice_number") or "N/A"
    invoice_amount = ext.get("amount") or 0
    currency = ext.get("currency") or "USD"
    due_date = ext.get("due_date") or "N/A"
    label = verdict_label(state.get("final_decision"))
    bg, color = OUTCOME_COLORS.get(label, ("#F3F4F6", "#374151"))

    # For revision delta payments, show the actual paid amount with a note
    paid_amount = payment.get("amount")
    is_delta = (
        paid_amount is not None and label == "PAID" and paid_amount != invoice_amount
    )
    amount_html = (
        f"${paid_amount:,.2f} {_esc(currency)}"
        f"<span style='font-size:0.78rem;color:#9CA3AF;font-weight:400;'>"
        f" (revision; invoice total ${invoice_amount:,.2f})</span>"
        if is_delta
        else f"${invoice_amount:,.2f} {_esc(currency)}"
    )

    return (
        "<div style='background:white;border:1px solid #E5E7EB;border-radius:14px;"
        "padding:24px 28px;box-shadow:0 2px 8px rgba(0,0,0,0.07);margin-bottom:16px;"
        "display:flex;justify-content:space-between;align-items:flex-start;gap:20px;'>"
        "<div style='min-width:0;'>"
        f"<div style='font-size:0.7rem;font-weight:600;color:#9CA3AF;letter-spacing:0.08em;"
        f"text-transform:uppercase;margin-bottom:8px;'>{_esc(inv_num)}</div>"
        f"<div style='font-size:1.65rem;font-weight:700;color:#111827;line-height:1.2;"
        f"margin-bottom:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
        f"{_esc(vendor)}</div>"
        f"<div style='display:flex;gap:20px;align-items:center;flex-wrap:wrap;'>"
        f"<span style='font-size:1rem;font-weight:600;color:#374151;'>"
        f"{amount_html}</span>"
        f"<span style='font-size:0.88rem;color:#9CA3AF;'>Due: {_esc(due_date)}</span>"
        f"</div></div>"
        "<div style='flex-shrink:0;margin-top:2px;'>"
        f"<div style='background:{bg};color:{color};font-size:0.78rem;font-weight:700;"
        f"padding:10px 22px;border-radius:24px;letter-spacing:0.06em;"
        f"white-space:nowrap;'>{label}</div>"
        "</div></div>"
    )


def issue_card(flag: dict) -> str:
    """Coloured block for a single validation flag."""
    severity = flag.get("severity", "warning")
    border_c, badge_bg, badge_fg, glyph = SEVERITY_THEME.get(
        severity, SEVERITY_THEME["warning"]
    )
    title = issue_label(flag.get("issue_type", ""))
    item = flag.get("item", "")
    detail = flag.get("detail", "")

    item_html = (
        f"<span style='font-size:0.76rem;color:#6B7280;margin-left:8px;'>"
        f"{_esc(item)}</span>"
        if item
        else ""
    )
    return (
        f"<div style='border-left:3px solid {border_c};background:#FAFAFA;"
        f"border-radius:0 8px 8px 0;padding:10px 14px;margin-bottom:8px;'>"
        f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:4px;'>"
        f"<span style='background:{badge_bg};color:{badge_fg};font-size:0.72rem;"
        f"font-weight:700;padding:2px 8px;border-radius:10px;'>"
        f"{glyph} {severity.upper()}</span>"
        f"<span style='font-size:0.84rem;font-weight:600;color:#111827;'>{_esc(title)}</span>"
        f"{item_html}</div>"
        f"<div style='font-size:0.82rem;color:#4B5563;line-height:1.5;'>{_esc(detail)}</div>"
        f"</div>"
    )


def kpi_card(label: str, value: str, color: str = "#2563EB") -> str:
    """Single metric tile for the history dashboard."""
    return (
        f"<div style='background:white;border:1px solid #E5E7EB;border-radius:10px;"
        f"padding:18px 20px;box-shadow:0 1px 3px rgba(0,0,0,0.05);'>"
        f"<div style='font-size:0.7rem;font-weight:700;color:#9CA3AF;text-transform:uppercase;"
        f"letter-spacing:0.07em;margin-bottom:8px;'>{label}</div>"
        f"<div style='font-size:1.55rem;font-weight:700;color:{color};'>{value}</div>"
        f"</div>"
    )
