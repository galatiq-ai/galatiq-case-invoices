-- Acme Corp legacy ERP export (SQLite).
-- Build a database with:
--   python -c "import sqlite3; sqlite3.connect('acme_erp.db').executescript(open('data/erp/acme_erp.sql').read())"
--
-- Your system must read ERP data from the file passed with --db. Reviewers run it
-- against a different file with this same schema and different records. You may add
-- your own tables (for example, to record what you've processed), but don't change
-- these ones.

PRAGMA foreign_keys = ON;

-- Vendor master. status is 'active', 'hold' or 'inactive'. Legacy duplicate records exist.
CREATE TABLE vendors (
    vendor_id      TEXT PRIMARY KEY,
    legal_name     TEXT NOT NULL,
    address        TEXT,
    tax_id         TEXT,
    currency       TEXT NOT NULL,        -- ISO 4217
    payment_terms  TEXT,                 -- e.g. 'Net 30'
    bank_routing   TEXT,                 -- US ABA routing number (USD vendors)
    bank_account   TEXT,                 -- account number, or IBAN for EUR vendors
    status         TEXT NOT NULL CHECK (status IN ('active', 'hold', 'inactive')),
    onboarded_on   TEXT NOT NULL         -- YYYY-MM-DD
);

-- Item master. Vendors don't always use these codes on their invoices.
CREATE TABLE items (
    item         TEXT PRIMARY KEY,
    description  TEXT NOT NULL,
    uom          TEXT NOT NULL            -- unit of measure: EA (each), BX (box)
);

-- Warehouse stock snapshot. Lags receipts and is often wrong (see AP policy 5.3).
CREATE TABLE inventory (
    item   TEXT PRIMARY KEY,
    stock  INTEGER
);

CREATE TABLE purchase_orders (
    po_number   TEXT PRIMARY KEY,
    vendor_id   TEXT NOT NULL REFERENCES vendors(vendor_id),
    order_date  TEXT NOT NULL,
    currency    TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('open', 'closed', 'cancelled'))
);

CREATE TABLE po_lines (
    po_number          TEXT NOT NULL REFERENCES purchase_orders(po_number),
    line_no            INTEGER NOT NULL,
    item               TEXT NOT NULL REFERENCES items(item),
    quantity           INTEGER NOT NULL,
    unit_price         REAL NOT NULL,      -- in the PO currency
    quantity_invoiced  INTEGER NOT NULL DEFAULT 0,  -- already invoiced and paid
    PRIMARY KEY (po_number, line_no)
);

CREATE TABLE goods_receipts (
    receipt_id   TEXT PRIMARY KEY,
    po_number    TEXT NOT NULL REFERENCES purchase_orders(po_number),
    item         TEXT NOT NULL REFERENCES items(item),
    quantity     INTEGER NOT NULL,
    received_on  TEXT NOT NULL
);

-- Invoices already paid before this batch.
CREATE TABLE payments (
    payment_id      TEXT PRIMARY KEY,
    vendor_id       TEXT NOT NULL REFERENCES vendors(vendor_id),
    invoice_number  TEXT NOT NULL,
    invoice_date    TEXT NOT NULL,
    po_number       TEXT REFERENCES purchase_orders(po_number),
    amount          REAL NOT NULL,
    currency        TEXT NOT NULL,
    paid_on         TEXT NOT NULL
);

-- USD value of one unit of currency, effective from rate_date.
CREATE TABLE fx_rates (
    currency      TEXT NOT NULL,
    rate_date     TEXT NOT NULL,
    usd_per_unit  REAL NOT NULL,
    PRIMARY KEY (currency, rate_date)
);


INSERT INTO vendors VALUES
    ('V001', 'Widgets Inc.', '100 Main St, Chicago, IL 60601', '36-4419020', 'USD', 'Net 15', '071921891', '4410294411', 'active', '2019-03-04'),
    ('V002', 'Gadgets Co.', '55 Harbor Way, Oakland, CA 94607', '94-3378125', 'USD', 'Net 30', '121143260', '7730017302', 'active', '2020-06-15'),
    ('V003', 'Precision Parts Ltd.', '742 Evergreen Terrace, Springfield, IL 62704', '37-1662093', 'USD', 'Net 30', '081909016', '2290611188', 'active', '2021-01-11'),
    ('V004', 'Acme Industrial Supplies', '9 Foundry Rd, Gary, IN 46402', '35-2208471', 'USD', 'Net 15', '074914229', '6615025521', 'active', '2018-09-30'),
    ('V005', 'MegaWidgets Corp', '4100 Industrial Pkwy, Toledo, OH 43612', '34-1987730', 'USD', 'Net 30', '041215663', '5088209034', 'active', '2022-02-01'),
    ('V006', 'Consolidated Materials Group', '1200 River Rd, Louisville, KY 40206', '61-1123845', 'USD', 'Net 30', '083908420', '3301776610', 'active', '2017-05-22'),
    ('V007', 'Summit Manufacturing Co.', '88 Summit Ave, Denver, CO 80202', '84-1550312', 'USD', 'Net 30', '102107970', '9120442047', 'active', '2020-10-05'),
    ('V008', 'FastShip Ltd.', '300 Dock St, Newark, NJ 07105', '22-3871106', 'USD', 'Net 30', '031207607', '1408823390', 'active', '2019-11-18'),
    ('V009', 'Atlas Industrial Supply', '500 Commerce Blvd, Detroit, MI 48201', '38-2716604', 'USD', 'Net 60', '072413829', '8873108125', 'active', '2016-04-12'),
    ('V010', 'TechParts International', 'Speicherstadt 12, 20457 Hamburg, Germany', 'DE298173645', 'EUR', 'Net 30', NULL, 'DE89370400440532013000', 'active', '2023-03-01'),
    ('V011', 'Reliable Components Inc.', '21 Elm St, Worcester, MA 01608', '04-3301987', 'USD', 'Net 30', '211370150', '6602194470', 'active', '2025-12-01'),
    ('V012', 'Keystone Tooling Inc.', '640 Mill St, Allentown, PA 18101', '23-2994410', 'USD', 'Net 30', '031302971', '2207615816', 'active', '2018-02-20'),
    ('V014', 'Widgets, Inc', '100 Main Street, Chicago IL', '36-4419020', 'USD', 'Net 30', '071921891', '1180330081', 'inactive', '2012-02-10');

INSERT INTO items VALUES
    ('WidgetA', 'Widget, type A', 'EA'),
    ('WidgetB', 'Widget, type B', 'EA'),
    ('GadgetX', 'Gadget, model X', 'EA'),
    ('BoltM8', 'Hex bolt M8x40, zinc plated, box of 100', 'BX'),
    ('BearingB6204', 'Ball bearing 6204-2RS', 'EA'),
    ('GasketG9', 'Gasket, nitrile (NBR), 90 mm', 'EA'),
    ('SensorK2', 'Proximity sensor K2', 'EA'),
    ('ValveV7', 'Ball valve 3/4 in, brass', 'EA');

INSERT INTO inventory VALUES
    ('WidgetA', 15),
    ('WidgetB', 10),
    ('GadgetX', 5),
    ('FakeItem', 0),
    ('BoltM8', 40),
    ('BearingB6204', 120),
    ('GasketG9', 2400),
    ('SensorK2', 12),
    ('ValveV7', 30);

INSERT INTO purchase_orders VALUES
    ('PO-20251105', 'V001', '2025-11-05', 'USD', 'closed'),
    ('PO-20251110', 'V002', '2025-11-10', 'USD', 'closed'),
    ('PO-20251201', 'V007', '2025-12-01', 'USD', 'closed'),
    ('PO-20251215', 'V006', '2025-12-15', 'USD', 'open'),
    ('PO-20260105', 'V001', '2026-01-05', 'USD', 'open'),
    ('PO-20260108', 'V007', '2026-01-08', 'USD', 'open'),
    ('PO-20260109', 'V009', '2026-01-09', 'USD', 'open'),
    ('PO-20260110', 'V003', '2026-01-10', 'USD', 'open'),
    ('PO-20260111', 'V010', '2026-01-11', 'EUR', 'open'),
    ('PO-20260112', 'V002', '2026-01-12', 'USD', 'open'),
    ('PO-20260113', 'V006', '2026-01-13', 'USD', 'open'),
    ('PO-20260114', 'V004', '2026-01-14', 'USD', 'open'),
    ('PO-20260115', 'V008', '2026-01-15', 'USD', 'open'),
    ('PO-20260116', 'V005', '2026-01-16', 'USD', 'open'),
    ('PO-20260119', 'V011', '2026-01-19', 'USD', 'open'),
    ('PO-20260120', 'V001', '2026-01-20', 'USD', 'open'),
    ('PO-20260121', 'V012', '2026-01-21', 'USD', 'open'),
    ('PO-20260122', 'V003', '2026-01-22', 'USD', 'open'),
    ('PO-20260123', 'V010', '2026-01-23', 'EUR', 'open'),
    ('PO-20260124', 'V005', '2026-01-24', 'USD', 'open');

INSERT INTO po_lines VALUES
    ('PO-20251105', 1, 'WidgetA', 5, 250.00, 5),
    ('PO-20251105', 2, 'WidgetB', 2, 500.00, 2),
    ('PO-20251110', 1, 'GadgetX', 10, 750.00, 10),
    ('PO-20251201', 1, 'WidgetA', 6, 250.00, 6),
    ('PO-20251201', 2, 'WidgetB', 6, 500.00, 6),
    ('PO-20251215', 1, 'BoltM8', 60, 42.00, 0),
    ('PO-20251215', 2, 'BearingB6204', 300, 18.50, 0),
    ('PO-20251215', 3, 'GasketG9', 900, 6.75, 0),
    ('PO-20260105', 1, 'WidgetA', 10, 250.00, 0),
    ('PO-20260105', 2, 'WidgetB', 5, 500.00, 0),
    ('PO-20260108', 1, 'WidgetA', 6, 250.00, 0),
    ('PO-20260108', 2, 'WidgetB', 3, 500.00, 0),
    ('PO-20260109', 1, 'WidgetA', 20, 250.00, 0),
    ('PO-20260109', 2, 'WidgetB', 20, 500.00, 0),
    ('PO-20260109', 3, 'GadgetX', 9, 750.00, 0),
    ('PO-20260110', 1, 'WidgetA', 3, 250.00, 0),
    ('PO-20260110', 2, 'WidgetB', 2, 500.00, 0),
    ('PO-20260110', 3, 'GadgetX', 5, 750.00, 0),
    ('PO-20260111', 1, 'WidgetA', 4, 225.00, 0),
    ('PO-20260111', 2, 'WidgetB', 6, 475.00, 0),
    ('PO-20260112', 1, 'GadgetX', 20, 750.00, 0),
    ('PO-20260113', 1, 'WidgetA', 12, 250.00, 0),
    ('PO-20260113', 2, 'WidgetB', 4, 500.00, 0),
    ('PO-20260113', 3, 'GadgetX', 2, 750.00, 0),
    ('PO-20260114', 1, 'WidgetA', 5, 250.00, 0),
    ('PO-20260114', 2, 'WidgetB', 3, 500.00, 0),
    ('PO-20260115', 1, 'WidgetA', 12, 250.00, 0),
    ('PO-20260115', 2, 'WidgetB', 7, 500.00, 0),
    ('PO-20260115', 3, 'GadgetX', 4, 750.00, 0),
    ('PO-20260116', 1, 'WidgetA', 20, 250.00, 0),
    ('PO-20260116', 2, 'WidgetB', 15, 500.00, 0),
    ('PO-20260116', 3, 'GadgetX', 3, 750.00, 0),
    ('PO-20260119', 1, 'WidgetA', 10, 250.00, 0),
    ('PO-20260119', 2, 'WidgetB', 5, 500.00, 0),
    ('PO-20260119', 3, 'GadgetX', 2, 750.00, 0),
    ('PO-20260120', 1, 'WidgetA', 4, 250.00, 0),
    ('PO-20260120', 2, 'WidgetB', 2, 500.00, 0),
    ('PO-20260121', 1, 'GadgetX', 6, 750.00, 0),
    ('PO-20260121', 2, 'WidgetB', 4, 500.00, 0),
    ('PO-20260122', 1, 'WidgetB', 6, 500.00, 0),
    ('PO-20260122', 2, 'GadgetX', 2, 750.00, 0),
    ('PO-20260123', 1, 'WidgetA', 20, 225.00, 0),
    ('PO-20260123', 2, 'WidgetB', 10, 475.00, 0),
    ('PO-20260124', 1, 'WidgetB', 20, 500.00, 0);

INSERT INTO goods_receipts VALUES
    ('GR-20251105-001', 'PO-20251105', 'WidgetA', 5, '2025-11-14'),
    ('GR-20251105-002', 'PO-20251105', 'WidgetB', 2, '2025-11-14'),
    ('GR-20251110-003', 'PO-20251110', 'GadgetX', 10, '2025-12-10'),
    ('GR-20251201-004', 'PO-20251201', 'WidgetA', 6, '2025-12-08'),
    ('GR-20251201-005', 'PO-20251201', 'WidgetB', 6, '2025-12-08'),
    ('GR-20251215-006', 'PO-20251215', 'BoltM8', 3, '2026-01-06'),
    ('GR-20251215-007', 'PO-20251215', 'BearingB6204', 23, '2026-01-06'),
    ('GR-20251215-008', 'PO-20251215', 'GasketG9', 51, '2026-01-06'),
    ('GR-20251215-009', 'PO-20251215', 'BoltM8', 5, '2026-01-08'),
    ('GR-20251215-010', 'PO-20251215', 'BearingB6204', 23, '2026-01-08'),
    ('GR-20251215-011', 'PO-20251215', 'GasketG9', 65, '2026-01-08'),
    ('GR-20251215-012', 'PO-20251215', 'BoltM8', 3, '2026-01-09'),
    ('GR-20251215-013', 'PO-20251215', 'BearingB6204', 19, '2026-01-09'),
    ('GR-20251215-014', 'PO-20251215', 'GasketG9', 44, '2026-01-09'),
    ('GR-20251215-015', 'PO-20251215', 'BoltM8', 4, '2026-01-15'),
    ('GR-20251215-016', 'PO-20251215', 'BearingB6204', 17, '2026-01-15'),
    ('GR-20251215-017', 'PO-20251215', 'GasketG9', 58, '2026-01-15'),
    ('GR-20251215-018', 'PO-20251215', 'BoltM8', 5, '2026-01-16'),
    ('GR-20251215-019', 'PO-20251215', 'BearingB6204', 24, '2026-01-16'),
    ('GR-20251215-020', 'PO-20251215', 'GasketG9', 59, '2026-01-16'),
    ('GR-20251215-021', 'PO-20251215', 'BoltM8', 5, '2026-01-17'),
    ('GR-20251215-022', 'PO-20251215', 'BearingB6204', 23, '2026-01-17'),
    ('GR-20251215-023', 'PO-20251215', 'GasketG9', 45, '2026-01-17'),
    ('GR-20251215-024', 'PO-20251215', 'BoltM8', 5, '2026-01-19'),
    ('GR-20251215-025', 'PO-20251215', 'BearingB6204', 21, '2026-01-19'),
    ('GR-20251215-026', 'PO-20251215', 'GasketG9', 70, '2026-01-19'),
    ('GR-20251215-027', 'PO-20251215', 'BoltM8', 4, '2026-01-22'),
    ('GR-20251215-028', 'PO-20251215', 'BearingB6204', 21, '2026-01-22'),
    ('GR-20251215-029', 'PO-20251215', 'GasketG9', 48, '2026-01-22'),
    ('GR-20251215-030', 'PO-20251215', 'BoltM8', 5, '2026-01-25'),
    ('GR-20251215-031', 'PO-20251215', 'BearingB6204', 15, '2026-01-25'),
    ('GR-20251215-032', 'PO-20251215', 'GasketG9', 59, '2026-01-25'),
    ('GR-20251215-033', 'PO-20251215', 'BoltM8', 4, '2026-01-27'),
    ('GR-20251215-034', 'PO-20251215', 'BearingB6204', 15, '2026-01-27'),
    ('GR-20251215-035', 'PO-20251215', 'GasketG9', 67, '2026-01-27'),
    ('GR-20251215-036', 'PO-20251215', 'BoltM8', 4, '2026-01-28'),
    ('GR-20251215-037', 'PO-20251215', 'BearingB6204', 21, '2026-01-28'),
    ('GR-20251215-038', 'PO-20251215', 'GasketG9', 58, '2026-01-28'),
    ('GR-20251215-039', 'PO-20251215', 'BoltM8', 3, '2026-01-30'),
    ('GR-20251215-040', 'PO-20251215', 'BearingB6204', 17, '2026-01-30'),
    ('GR-20251215-041', 'PO-20251215', 'GasketG9', 51, '2026-01-30'),
    ('GR-20260105-042', 'PO-20260105', 'WidgetA', 10, '2026-01-12'),
    ('GR-20260105-043', 'PO-20260105', 'WidgetB', 5, '2026-01-12'),
    ('GR-20260108-044', 'PO-20260108', 'WidgetA', 6, '2026-01-16'),
    ('GR-20260108-045', 'PO-20260108', 'WidgetB', 3, '2026-01-16'),
    ('GR-20260109-046', 'PO-20260109', 'WidgetA', 20, '2026-01-22'),
    ('GR-20260109-047', 'PO-20260109', 'WidgetB', 18, '2026-01-22'),
    ('GR-20260109-048', 'PO-20260109', 'GadgetX', 9, '2026-01-22'),
    ('GR-20260110-049', 'PO-20260110', 'WidgetA', 3, '2026-01-20'),
    ('GR-20260110-050', 'PO-20260110', 'WidgetB', 2, '2026-01-20'),
    ('GR-20260110-051', 'PO-20260110', 'GadgetX', 5, '2026-01-28'),
    ('GR-20260111-052', 'PO-20260111', 'WidgetA', 4, '2026-01-24'),
    ('GR-20260111-053', 'PO-20260111', 'WidgetB', 6, '2026-01-24'),
    ('GR-20260112-054', 'PO-20260112', 'GadgetX', 12, '2026-01-20'),
    ('GR-20260112-055', 'PO-20260112', 'GadgetX', 8, '2026-01-26'),
    ('GR-20260113-056', 'PO-20260113', 'WidgetA', 12, '2026-01-24'),
    ('GR-20260113-057', 'PO-20260113', 'WidgetB', 4, '2026-01-24'),
    ('GR-20260113-058', 'PO-20260113', 'GadgetX', 2, '2026-01-24'),
    ('GR-20260114-059', 'PO-20260114', 'WidgetA', 5, '2026-01-22'),
    ('GR-20260114-060', 'PO-20260114', 'WidgetB', 3, '2026-01-22'),
    ('GR-20260115-061', 'PO-20260115', 'WidgetA', 12, '2026-01-23'),
    ('GR-20260115-062', 'PO-20260115', 'WidgetB', 7, '2026-01-23'),
    ('GR-20260115-063', 'PO-20260115', 'GadgetX', 4, '2026-01-23'),
    ('GR-20260116-064', 'PO-20260116', 'WidgetA', 20, '2026-01-27'),
    ('GR-20260116-065', 'PO-20260116', 'WidgetB', 15, '2026-01-27'),
    ('GR-20260116-066', 'PO-20260116', 'GadgetX', 3, '2026-01-27'),
    ('GR-20260119-067', 'PO-20260119', 'WidgetA', 10, '2026-01-27'),
    ('GR-20260119-068', 'PO-20260119', 'WidgetB', 5, '2026-01-27'),
    ('GR-20260119-069', 'PO-20260119', 'GadgetX', 2, '2026-01-27'),
    ('GR-20260120-070', 'PO-20260120', 'WidgetA', 4, '2026-01-26'),
    ('GR-20260120-071', 'PO-20260120', 'WidgetB', 2, '2026-01-26'),
    ('GR-20260121-072', 'PO-20260121', 'GadgetX', 6, '2026-01-30'),
    ('GR-20260121-073', 'PO-20260121', 'WidgetB', 4, '2026-01-30'),
    ('GR-20260122-074', 'PO-20260122', 'WidgetB', 6, '2026-01-30'),
    ('GR-20260122-075', 'PO-20260122', 'GadgetX', 2, '2026-01-30'),
    ('GR-20260123-076', 'PO-20260123', 'WidgetA', 20, '2026-02-02'),
    ('GR-20260123-077', 'PO-20260123', 'WidgetB', 10, '2026-02-02'),
    ('GR-20260124-078', 'PO-20260124', 'WidgetB', 6, '2026-02-02');

INSERT INTO payments VALUES
    ('PAY-25-1150', 'V001', 'INV-0950', '2025-11-20', 'PO-20251105', 2250.00, 'USD', '2025-12-05'),
    ('PAY-25-1188', 'V007', 'INV-0981', '2025-12-10', 'PO-20251201', 4500.00, 'USD', '2025-12-29'),
    ('PAY-26-0004', 'V002', 'INV-0974', '2025-12-18', 'PO-20251110', 7500.00, 'USD', '2026-01-09');

INSERT INTO fx_rates VALUES
    ('EUR', '2025-12-01', 1.16),
    ('EUR', '2026-01-01', 1.17),
    ('EUR', '2026-02-01', 1.18);
