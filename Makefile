# Galatiq Invoice Agent — developer convenience targets
# Requires: uv (https://docs.astral.sh/uv/getting-started/installation/)

.PHONY: install run-example run-batch ui lint format test test-e2e test-all db-reset clean help


# ── Setup ────────────────────────────────────────────────────────────────────

install:        ## Install all dependencies with uv
	uv sync

# ── Run ──────────────────────────────────────────────────────────────────────

run-example:    ## Process a single invoice (INV-1001) via CLI
	uv run python main.py --invoice_path=data/invoices/invoice_1001.txt

run-batch:      ## Process all invoices in data/invoices/ (batch mode)
	uv run python main.py --batch

ui:             ## Launch the Streamlit UI (http://localhost:8501)
	uv run streamlit run app.py

# ── Lint & format ─────────────────────────────────────────────────────────────

lint:           ## Check code with ruff (no changes)
	uv run ruff check .

format:         ## Auto-fix lint issues and format with ruff
	uv run ruff check . --fix
	uv run ruff format .

# ── Tests ─────────────────────────────────────────────────────────────────────

test:           ## Run all unit tests (no API key needed)
	uv run pytest tests/ -v

test-e2e:       ## Run end-to-end tests (requires API key + INTEGRATION_TESTS=1)
	INTEGRATION_TESTS=1 uv run pytest tests/test_end_to_end.py -v

test-all:       ## Run unit + end-to-end tests
	INTEGRATION_TESTS=1 uv run pytest tests/ -v

# ── Database ──────────────────────────────────────────────────────────────────

db-reset:       ## Wipe the approved_quantities table
	uv run python main.py --reset_db

# ── Utility ───────────────────────────────────────────────────────────────────

clean:          ## Stop Streamlit, wipe acme.db, run logs, and caches
	-pkill -f "streamlit run app.py" 2>/dev/null; sleep 1
	rm -f acme.db acme.db-wal acme.db-shm
	rm -rf runs/
	rm -rf .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

help:           ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
