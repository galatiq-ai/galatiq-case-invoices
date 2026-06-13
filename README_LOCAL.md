# Local Development & Testing — Invoice Processor

This document explains the implementation at a high level and provides step-by-step instructions to run and test the system locally.

**Overview**

- The project is a multi-agent invoice processing pipeline with four main agents:
  - `ingest_agent` — extracts structured invoice data (LLM-driven with deterministic fallback).
  - `validate_agent` — deterministic validation against the inventory DB.
  - `approval_agent` — rule-based approval plus optional LLM critique loop.
  - `payment_agent` — mock payment execution and mark invoices processed.

- State models are defined in `state.py` (Pydantic models). Traces of each agent run are written to `traces/` (per-invoice JSON). A simple audit table is stored in `logs/agent_logs.db`.

- The deterministic extraction supports TXT, CSV, JSON, XML and includes heuristics for OCR artifacts, bulleted item lists, and email-body formatted invoices. PDF text extraction now also tries an OCR fallback when page text is empty and optional OCR packages are installed.

**Architecture**

- The app is organized as a LangGraph state machine in `graph.py`.
- Each stage is a node that reads the shared state, adds its own results, and passes that state forward.
- `state.py` defines the structured schema exchanged between stages.
- `database.py` owns SQLite access for inventory, known vendors, processed invoices, and audit logs.
- `tools.py` contains shared helpers for file loading, PDF handling, mock payment, and trace writing.
- `main.py` is the CLI entrypoint, and `streamlit_app.py` reuses the same pipeline for the UI.

**Dataflow**

1. `main.py` or `streamlit_app.py` calls `run_pipeline()` in `graph.py` with an invoice file path.
2. `ingest_agent` reads the file, extracts structured invoice data, and stores it in `extracted_invoice`.
3. `validate_agent` checks the invoice against the local SQLite inventory and writes `validation_result`.
4. `approval_agent` applies business rules and critique logic to produce `approval_decision`.
5. If approved, `payment_agent` executes the mock payment and stores `payment_result`.
6. Each stage writes a trace snapshot to `traces/`, and approval/payment also write audit rows to `logs/agent_logs.db`.

**Functionalities Covered**

- Invoice ingestion from TXT, CSV, JSON, XML, and PDF files.
- Normalization for OCR artifacts, relative dates, email headers, and bulleted item lists.
- Validation against a local SQLite inventory database, including:
  - known items
  - stock mismatches
  - unknown items
  - zero-stock items
  - negative quantities and other integrity issues
- Approval behavior with:
  - high-value scrutiny for invoices over $10K
  - known-vendor checks
  - duplicate invoice detection
  - urgency/fraud-language detection
  - approve, hold, and reject outcomes
  - correction hints for re-ingestion
- Mock payment execution for approved invoices, with skipped processing for held or rejected invoices.
- CLI batch mode with CSV export.
- A lightweight Streamlit UI for interactive review.
- Trace logging and SQLite audit logging for debugging and review.

**Key files**

- `main.py` — CLI entrypoint and batch runner.
- `graph.py` — pipeline orchestration (LangGraph-style graph and print summary).
- `agents/` — contains the four agents described above.
- `state.py` — Pydantic models and shared state schema.
- `database.py` — SQLite helpers: inventory, vendors, processed invoices, and audit logs.
- `scripts/generate_schemas.py` — generates JSON schema files to `schemas/` from Pydantic models.
- `tests/` — pytest test suite (unit + e2e).

**Environment & Dependencies (local)**

1. Create and activate a virtual environment (Windows example):

```powershell
python -m venv .venv
.\.venv\Scripts\activate
```

(On macOS/Linux use `python -m venv .venv` then `source .venv/bin/activate`)

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Optional: If you need the LLM integration, create a `.env` file next to `main.py` with keys (do NOT commit this file). The project prefers xAI Grok when an `XAI_API_KEY` is present; leaving that value empty enables deterministic fallback.

```
# .env (do not commit)
# Recommended (Grok-first):
XAI_API_KEY=xai-...        # xAI Grok API key (set this to enable Grok)
XAI_MODEL=grok-3           # recommended Grok model

# Optional fallback for local experimentation (uncomment if needed):
# OPENAI_API_KEY=sk-...
# OPENAI_MODEL=gpt-4o-mini
```

The code uses `config.get_llm()` to return a Grok-backed LangChain-compatible client when `XAI_API_KEY` is present; otherwise it falls back to deterministic extraction/critique. OpenAI is only used as an optional fallback for local experimentation if you explicitly set `OPENAI_API_KEY`.

**Initialize / Seed DB**

The project will create the `storage/` DB directory automatically. To seed the DB (inventory + vendors):

```bash
python main.py --seed
or 
python -m scripts.seed_inventory
```

When you run`python main.py --seed` it will call `init_db()`.

**Generate JSON Schemas**

Run the schema generator (creates files in `schemas/`):

```bash
python scripts/generate_schemas.py
```

**Streamlit UI**

A lightweight Streamlit UI is provided in `streamlit_app.py`. The app lets you upload or
select a sample invoice and runs the same `run_pipeline()` pipeline used by the CLI,
showing the extracted invoice, validation, approval and payment results as JSON plus a
simple trace log.

To run the UI locally after installing requirements:

```bash
streamlit run streamlit_app.py
```

Open the browser at `http://localhost:8501`.

**Run the pipeline (CLI)**

```bash
python main.py --invoice_path=data/invoices/invoice_1001.txt
```

- Add `--verbose` to show additional debug detail for each agent:

```bash
python main.py --invoice_path=data/invoices/invoice_1001.txt --verbose
```

**Run the pipeline (batch + CSV export)**

Process an entire invoices directory and write results to CSV:

```bash
python main.py --invoice_dir=data/invoices --output_csv=results.csv
```

`results.csv` will contain one row per invoice with these columns:
`file, invoice_number, vendor, total, currency, decision, payment_status, flags, error`.

**View audit logs**

A central audit DB is written to `logs/agent_logs.db`. Quick inspection (Python snippet):

```python
import sqlite3
from pathlib import Path
p = Path('logs/agent_logs.db')
conn = sqlite3.connect(str(p))
for r in conn.execute('SELECT * FROM agent_logs'):
    print(r)
conn.close()
```

**Running tests**

Run the full test suite with pytest:

```bash
pytest tests/ -v
```

Run a single test file:

```bash
pytest tests/test_validate.py -q
```

With coverage (pytest-cov must be installed):

```bash
pytest --cov=agents --cov=database --cov=tools -v
```



**Troubleshooting & Notes**

- Console encoding on Windows: the code avoids using non-ASCII icons in output to prevent encoding errors on some Windows consoles. If you see encoding errors, run Python from a UTF-8 enabled terminal or change `PYTHONIOENCODING=utf-8`.

- LLM calls: If LLM keys are absent or invalid, the code falls back to deterministic logic and appends an ingestion/critique message to traces. Correction loops preserve actionable hints from approval and feed them back into re-ingestion when needed.

- PDF OCR fallback: if you need scanned-PDF support, install `pytesseract` plus the system Tesseract binary. Without those optional pieces, the app still tries normal text extraction first and then falls back to pdfplumber.

- Traces are stored in `traces/` named per-invoice (e.g., `INV-1001.json` or `unknown.json`). Use these to inspect agent-level state snapshots.

- If you add or update dependencies, remember to re-run `pip install -r requirements.txt` inside the virtual environment.

**Where to look when things go wrong**

- `traces/` — agent-by-agent state snapshots during pipeline runs.
- `logs/agent_logs.db` — central audit log of agent decisions.
- `storage/` — SQLite DB(s) used by the app (inventory, vendors, processed invoices).
- `tests/` — example inputs and expected outputs for behavior verification.

---

Created for local dev and QA. For quick start, run:

```bash
python -m venv .venv
.\.venv\Scripts\activate    # or `source .venv/bin/activate`
pip install -r requirements.txt
python scripts/seed_inventory.py
pytest tests/ -v
python main.py --invoice_path=data/invoices/invoice_1001.txt --verbose
```
