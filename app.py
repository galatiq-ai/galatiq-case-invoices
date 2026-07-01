"""Streamlit entry point for InvoiceAI."""

from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from config import INVOICE_DIR  # noqa: E402
from src.db.queries import init_normalized_db  # noqa: E402
from src.graph.graph import stream_pipeline  # noqa: E402
from src.ops_db import init_db  # noqa: E402
from ui.cards import active_stage, app_logo, pipeline_tracker  # noqa: E402
from ui.labels import LLM_PROVIDERS, NODE_STEP_MAP  # noqa: E402
from ui.views import show_history, show_result, splash  # noqa: E402

init_db()
init_normalized_db()

st.set_page_config(
    page_title="InvoiceAI",
    page_icon=":material/receipt_long:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
@keyframes pulse-dot {
    0%, 100% { transform: scale(1);    opacity: 1;   }
    50%       { transform: scale(1.4); opacity: 0.45; }
}
.dot-active {
    animation: pulse-dot 1.1s ease-in-out infinite;
    display: inline-block;
    width: 22px; height: 22px;
    background: #2563EB;
    border-radius: 50%;
    flex-shrink: 0;
}
</style>
""",
    unsafe_allow_html=True,
)

# ── Session state ──────────────────────────────────────────────────────────────

if "history" not in st.session_state:
    st.session_state.history: list[dict] = []
if "started" not in st.session_state:
    st.session_state.started = False
if "processing" not in st.session_state:
    st.session_state.processing = False
if "pending_invoice" not in st.session_state:
    st.session_state.pending_invoice: str | None = None
if "running" not in st.session_state:
    st.session_state.running = False


# ── Helpers ────────────────────────────────────────────────────────────────────


def _next_invoice_path(suffix: str) -> Path:
    """Auto-increment invoice filename within INVOICE_DIR."""
    invoice_dir = Path(INVOICE_DIR)
    invoice_dir.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(r"^invoice_(\d+)\..+$", re.IGNORECASE)
    max_n = 1000
    for p in invoice_dir.iterdir():
        m = pattern.match(p.name)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return invoice_dir / f"invoice_{max_n + 1}{suffix}"


def _process_invoice(
    invoice_path: str, tmp_path: str | None, tab_ph: "st.delta_generator.DeltaGenerator"
) -> None:
    """Run the pipeline and update the tab placeholder as each stage completes."""
    run_id = (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
    )

    tab_ph.empty()
    with tab_ph.container():
        st.markdown(
            "<p style='color:#6B7280;font-size:0.88rem;margin-bottom:12px;'>"
            "Processing invoice...</p>",
            unsafe_allow_html=True,
        )
        tracker_ph = st.empty()

    tracker_ph.markdown(pipeline_tracker([], active_stage([])), unsafe_allow_html=True)

    seen: list[str] = []
    final_state: dict = {}
    t0 = time.monotonic()

    try:
        for node_name, state in stream_pipeline(
            {"invoice_path": invoice_path, "run_id": run_id}
        ):
            final_state = state
            step = NODE_STEP_MAP.get(node_name)
            if step and step not in seen:
                seen.append(step)
                tracker_ph.markdown(
                    pipeline_tracker(seen, active_stage(seen)), unsafe_allow_html=True
                )
    except Exception as exc:
        with tab_ph.container():
            st.error(f"Processing failed: {exc}")
        return
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    elapsed = time.monotonic() - t0
    final_state["_elapsed"] = elapsed
    st.session_state.history.append(final_state)

    with tab_ph.container():
        show_result(final_state, elapsed)


# ── Main app ───────────────────────────────────────────────────────────────────


def _main_app() -> None:
    invoice_path: str | None = None
    tmp_path: str | None = None

    with st.sidebar:
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:2px;'>"
            f"{app_logo(18, 6)}"
            f"<span style='font-size:1.15rem;font-weight:700;'>InvoiceAI</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.divider()

        mode = st.radio(
            "Input method",
            ["Pick an invoice", "Upload new invoice"],
            label_visibility="collapsed",
        )

        if mode == "Pick an invoice":
            files = sorted(
                str(p)
                for p in Path(INVOICE_DIR).glob("*")
                if p.suffix.lower() in {".txt", ".csv", ".json", ".pdf", ".xml"}
            )
            if files:
                invoice_path = st.selectbox(
                    "Pick an invoice",
                    files,
                    format_func=lambda p: Path(p).name,
                    label_visibility="collapsed",
                )
            else:
                st.warning(f"No invoices found in {INVOICE_DIR}")
        else:
            uploaded = st.file_uploader(
                "Upload new invoice",
                type=["txt", "csv", "json", "pdf", "xml"],
                label_visibility="collapsed",
            )
            if uploaded:
                cache_key = f"_saved_upload_{uploaded.name}_{uploaded.size}"
                if cache_key not in st.session_state:
                    saved_path = _next_invoice_path(Path(uploaded.name).suffix)
                    saved_path.write_bytes(uploaded.read())
                    st.session_state[cache_key] = str(saved_path)
                invoice_path = st.session_state[cache_key]
                st.success(f"Saved as `{Path(invoice_path).name}`")

        st.divider()
        process_btn = st.button(
            "Process",
            type="primary",
            disabled=invoice_path is None or st.session_state.running,
            width="stretch",
        )

        provider_key = os.getenv("LLM_PROVIDER", "nvidia").lower()
        provider_display = LLM_PROVIDERS.get(provider_key, provider_key.upper())
        st.markdown("<br>" * 6, unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.68rem;color:#C8CBD3;padding-bottom:4px;'>"
            f"Provider: {provider_display}</div>",
            unsafe_allow_html=True,
        )

    # When Process is clicked, store the path and trigger a clean rerun so the
    # old result disappears before the pipeline starts (no gray blur).
    if process_btn and invoice_path:
        st.session_state.pending_invoice = invoice_path
        st.session_state.processing = True
        st.session_state.running = True
        st.rerun()

    process_tab, history_tab = st.tabs(["Invoice Processing", "Invoice History"])

    with process_tab:
        if st.session_state.processing and st.session_state.pending_invoice:
            # Blank slate - no old content rendered, just the streaming placeholder
            path = st.session_state.pending_invoice
            st.session_state.processing = False
            st.session_state.pending_invoice = None
            tab_ph = st.empty()
            try:
                _process_invoice(path, tmp_path, tab_ph)
            finally:
                st.session_state.running = False
            st.rerun()
        elif st.session_state.history:
            # Render directly (no empty wrapper) so Streamlit diffs cleanly on next run
            show_result(
                st.session_state.history[-1],
                st.session_state.history[-1].get("_elapsed"),
            )
        else:
            st.markdown("<br>", unsafe_allow_html=True)
            st.info("Select an invoice from the sidebar and click Process to begin.")

    with history_tab:
        show_history()


# ── Entry point ────────────────────────────────────────────────────────────────

if not st.session_state.started:
    splash()
else:
    _main_app()
