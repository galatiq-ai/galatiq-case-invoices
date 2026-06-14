-- Database schema: observability (infra) + the invoice domain.
-- Applied idempotently on first connect (backend/db.py). Reference data lives in
-- seed.sql; the invoice-processing tables are written by the pipeline.

------------------------------------------------------------------------------
-- Observability
------------------------------------------------------------------------------

-- One structured row per request or job. trace_id links a wide event to the
-- per-invoice audit trail in invoice_trace.
CREATE TABLE IF NOT EXISTS wide_events (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    type        TEXT NOT NULL,
    level       TEXT NOT NULL,
    source      TEXT NOT NULL,
    path        TEXT,
    method      TEXT,
    status_code INTEGER,
    duration_ms REAL,
    error       INTEGER NOT NULL DEFAULT 0,
    data        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wide_events_trace ON wide_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_wide_events_created ON wide_events(created_at DESC);

-- Transitional: backs the scaffold hello demo. Removed when invoice endpoints land.
CREATE TABLE IF NOT EXISTS greetings (
    id      INTEGER PRIMARY KEY,
    message TEXT NOT NULL
);

------------------------------------------------------------------------------
-- Reference / master data (seeded — Acme's existing records)
------------------------------------------------------------------------------

-- Vendor master: only onboarded, active vendors are payable. A vendor absent
-- here is the unknown-vendor signal a fabricated invoice trips.
CREATE TABLE IF NOT EXISTS vendors (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'active',   -- active | inactive
    currency   TEXT NOT NULL DEFAULT 'USD',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Prior / alternate vendor names ("formerly FastShip Ltd.") so a rename doesn't
-- read as a new, unknown vendor.
CREATE TABLE IF NOT EXISTS vendor_aliases (
    alias     TEXT PRIMARY KEY,
    vendor_id INTEGER NOT NULL REFERENCES vendors(id)
);

-- Purchase orders: the authorization leg, and the only catalog that matters.
-- An invoice line is legitimate because we ordered it here — not because a name
-- appears in some item master, and not because we hold stock. This is accounts
-- payable, not inventory management.
CREATE TABLE IF NOT EXISTS purchase_orders (
    id         INTEGER PRIMARY KEY,
    po_number  TEXT NOT NULL UNIQUE,
    vendor_id  INTEGER NOT NULL REFERENCES vendors(id),
    status     TEXT NOT NULL DEFAULT 'open',     -- open | closed
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- qty_invoiced is cumulative consumption across paid invoices, so repeated or
-- over-billing against one PO is caught even when each invoice looks fine alone.
CREATE TABLE IF NOT EXISTS po_lines (
    id           INTEGER PRIMARY KEY,
    po_id        INTEGER NOT NULL REFERENCES purchase_orders(id),
    item         TEXT NOT NULL,                  -- authorized item; the PO line is the catalog of record
    qty_ordered  INTEGER NOT NULL,
    qty_invoiced INTEGER NOT NULL DEFAULT 0,
    unit_price   REAL NOT NULL                   -- authorized price
);

CREATE INDEX IF NOT EXISTS idx_po_vendor ON purchase_orders(vendor_id);
CREATE INDEX IF NOT EXISTS idx_po_lines_po ON po_lines(po_id);

------------------------------------------------------------------------------
-- Invoice processing (written by the pipeline)
------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS invoices (
    id              INTEGER PRIMARY KEY,
    trace_id        TEXT NOT NULL,               -- links wide_events + invoice_trace
    status          TEXT NOT NULL,               -- state machine (backend/statuses.py)

    source_path     TEXT,
    source_format   TEXT,                        -- txt | csv | json | xml | pdf

    invoice_number  TEXT,
    vendor_raw      TEXT,                         -- vendor name as extracted
    vendor_id       INTEGER REFERENCES vendors(id),  -- matched master id, null until/unless matched
    currency        TEXT,
    invoice_date    TEXT,
    due_date        TEXT,
    po_reference    TEXT,
    revision        TEXT,

    -- Stated figures, verbatim from the document — never recomputed, so the
    -- verify step can compare the document's own arithmetic against the lines.
    stated_subtotal REAL,
    stated_tax      REAL,
    stated_total    REAL,

    recommendation  TEXT,                         -- LLM advisory: approve | reject | needs_review
    outcome         TEXT,                         -- deterministic gate result / terminal disposition
    review_tier     TEXT,                         -- low | medium | high (deterministic triage)

    fingerprint     TEXT,                         -- vendor+number+amount, for exact-duplicate control
    superseded_by   INTEGER REFERENCES invoices(id),

    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id           INTEGER PRIMARY KEY,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(id),
    item_raw     TEXT NOT NULL,                   -- as written on the document
    matched_item TEXT,                            -- catalog identity after matching, null if unmatched
    quantity     REAL,
    unit_price   REAL,
    line_total   REAL,
    note         TEXT
);

-- Deterministic and LLM findings. severity drives the gate; code + severity
-- drive the deterministic review triage tier.
CREATE TABLE IF NOT EXISTS findings (
    id         INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    code       TEXT NOT NULL,                     -- unknown_vendor, qty_over_stock, ...
    severity   TEXT NOT NULL,                     -- info | warning | error
    message    TEXT NOT NULL,
    details    TEXT NOT NULL DEFAULT '{}',        -- JSON
    source     TEXT NOT NULL DEFAULT 'deterministic',  -- deterministic | llm
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-invoice audit trail: every stage, LLM exchange, tool call, gate decision,
-- and state transition. Survives across requests (initial run + review resume).
CREATE TABLE IF NOT EXISTS invoice_trace (
    id         INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    trace_id   TEXT NOT NULL,
    seq        INTEGER NOT NULL,                  -- order within the invoice
    stage      TEXT NOT NULL,                     -- ingest | extract | verify | validate | propose | ...
    kind       TEXT NOT NULL,                     -- llm | tool | gate | transition | note
    payload    TEXT NOT NULL DEFAULT '{}',        -- JSON
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status);
CREATE INDEX IF NOT EXISTS idx_invoices_trace ON invoices(trace_id);
CREATE INDEX IF NOT EXISTS idx_invoices_fingerprint ON invoices(fingerprint);
CREATE INDEX IF NOT EXISTS idx_findings_invoice ON findings(invoice_id);
CREATE INDEX IF NOT EXISTS idx_trace_invoice ON invoice_trace(invoice_id, seq);
