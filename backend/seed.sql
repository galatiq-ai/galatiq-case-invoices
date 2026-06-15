-- Reference data: Acme's existing vendor master and open POs. Idempotent
-- (INSERT OR IGNORE). This is what invoices are corroborated against, so it's
-- deliberately grounded in the provided invoice set:
--   * onboarded vendors -> payable; absent vendors (Fraudster LLC, NoProd
--     Industries) -> unknown-vendor signal.
--   * an item is authorized only by appearing on a PO line; items on no PO
--     (WidgetC, SuperGizmo, MegaSprocket) -> unknown-item signal.
--   * POs back the clean invoices (1001/1004/1006/1011/1015) at authorized
--     price and quantity, so they can clear touchless. Onboarded vendors
--     without a PO, and over-authorization quantities, surface in validation
--     rather than here.
-- No inventory table: this is accounts payable, not inventory management — an
-- invoice is corroborated by vendor + PO, not by warehouse stock-on-hand.

------------------------------------------------------------------------------
-- Vendor master
------------------------------------------------------------------------------
INSERT OR IGNORE INTO vendors (id, name, status, currency) VALUES
    (1,  'Widgets Inc.',                 'active', 'USD'),
    (2,  'Precision Parts Ltd.',         'active', 'USD'),
    (3,  'Acme Industrial Supplies',     'active', 'USD'),
    (4,  'Summit Manufacturing Co.',     'active', 'USD'),
    (5,  'Reliable Components Inc.',      'active', 'USD'),
    (6,  'Gadgets Co.',                   'active', 'USD'),
    (7,  'MegaWidgets Corp',             'active', 'USD'),
    (8,  'Consolidated Materials Group', 'active', 'USD'),
    (9,  'QuickShip Distributers',       'active', 'USD'),
    (10, 'Atlas Industrial Supply',      'active', 'USD'),
    (11, 'TechParts International',       'active', 'EUR'),
    (12, 'Global Supply Chain Partners', 'active', 'USD');

-- QuickShip Distributers was formerly FastShip Ltd.
INSERT OR IGNORE INTO vendor_aliases (alias, vendor_id) VALUES
    ('FastShip Ltd.', 9);

------------------------------------------------------------------------------
-- Open purchase orders backing the clean invoices
------------------------------------------------------------------------------
INSERT OR IGNORE INTO purchase_orders (id, po_number, vendor_id, status) VALUES
    (1, 'PO-2026-0001', 1, 'open'),   -- Widgets Inc.            -> INV-1001
    (2, 'PO-2026-0002', 2, 'open'),   -- Precision Parts Ltd.    -> INV-1004
    (3, 'PO-2026-0003', 3, 'open'),   -- Acme Industrial Supplies-> INV-1006
    (4, 'PO-2026-0004', 4, 'open'),   -- Summit Manufacturing Co.-> INV-1011
    (5, 'PO-2026-0005', 5, 'open');   -- Reliable Components Inc.-> INV-1015

INSERT OR IGNORE INTO po_lines (id, po_id, item, qty_ordered, qty_invoiced, unit_price) VALUES
    -- PO-2026-0001  (INV-1001)
    (1,  1, 'WidgetA', 10, 0, 250.00),
    (2,  1, 'WidgetB',  5, 0, 500.00),
    -- PO-2026-0002  (INV-1004)
    (3,  2, 'WidgetA',  3, 0, 250.00),
    (4,  2, 'WidgetB',  2, 0, 500.00),
    -- PO-2026-0003  (INV-1006)
    (5,  3, 'WidgetA',  5, 0, 250.00),
    (6,  3, 'WidgetB',  3, 0, 500.00),
    -- PO-2026-0004  (INV-1011)
    (7,  4, 'WidgetA',  6, 0, 250.00),
    (8,  4, 'WidgetB',  3, 0, 500.00),
    -- PO-2026-0005  (INV-1015)
    (9,  5, 'WidgetA', 10, 0, 250.00),
    (10, 5, 'WidgetB',  5, 0, 500.00),
    (11, 5, 'GadgetX',  2, 0, 750.00);
