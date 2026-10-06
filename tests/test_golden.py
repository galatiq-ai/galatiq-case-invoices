"""Slice 1 golden pipeline test: the offline batch over data/invoices/ (read-only).

Expectations come from `tests/golden` (transcribed from the cli-runner spec table), never from
observed output. Slice-1 rows are 1a (TXT, JSON, PDF) and 1b (CSV). Rows owned by slice 3
(Revision, Superseded, XML in EUR) are not asserted here;
`test_later_slices_are_deferred_explicitly` pins that partition.
"""

import argparse
import dataclasses
import json
import socket
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from invoice_pipeline import ledger, service
from invoice_pipeline.model import FindingCode as F
from tests.golden import ROWS, Final, rows_for

CORPUS = Path(__file__).parent.parent / "data" / "invoices"
ROWS_1A = sorted(rows_for("1a") + rows_for("1b"), key=lambda r: r.arrival)  # slice 1 rows
STATE = {
    Final.PAID: "paid",
    Final.NEEDS_REVIEW: "needs_review",
    Final.LOGGED_REJECTION: "logged_rejection",
    Final.DUPLICATE: "duplicate",
    Final.SUPERSEDED: "superseded",
}
row_ids = [f"row{r.n}-{r.arrival}" for r in ROWS_1A]


class Run:
    """The slice-1 corpus batch on fresh temp databases with a recording, succeeding bank."""

    def __init__(self, tmp_path):
        args = argparse.Namespace(
            llm="offline", ledger=tmp_path / "ledger.db", inventory=tmp_path / "inventory.db"
        )
        self.calls = []
        self.rt = dataclasses.replace(service.bootstrap(args), pay_fn=self.bank)
        self.paths = [CORPUS / r.arrival for r in ROWS_1A]
        self.batch = service.run_batch(self.paths, self.rt)
        self.by_file = {r.source: r for r in self.batch.results}

    def bank(self, vendor, amount, currency):
        self.calls.append((vendor, amount, currency))
        return {"status": "success"}

    def db(self):
        conn = ledger.connect(self.rt.ledger_path)
        try:
            return {r["id"]: r for r in conn.execute("SELECT * FROM arrivals ORDER BY id")}
        finally:
            conn.close()

    def row_of(self, arrival_file):
        return self.db()[self.by_file[arrival_file].arrival_id]


@pytest.fixture
def run(tmp_path, no_network):
    return Run(tmp_path)


def test_the_batch_is_the_slice_1_rows_in_lexical_arrival_order_and_none_fail(run):
    assert run.paths == sorted(run.paths)
    assert [r.n for r in ROWS_1A] == [1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 19, 20]
    assert run.batch.failed == [] and len(run.batch.results) == len(ROWS_1A)
    assert [r.source for r in run.batch.results] == [r.arrival for r in ROWS_1A]


def test_later_slices_are_deferred_explicitly():
    assert [r.n for r in rows_for("1b")] == [7, 8, 19]  # CSV: README scenario 1006 lives here
    assert not {r.n for r in rows_for("3")} & {r.n for r in ROWS_1A}
    assert [r.n for r in rows_for("3")] == [5, 16, 17, 18]  # Revision, Superseded, EUR XML
    assert len(ROWS_1A) + len(rows_for("3")) == len(ROWS) == 20


@pytest.mark.parametrize("row", ROWS_1A, ids=row_ids)
def test_each_row_ends_in_its_source_derived_outcome(run, row):
    assert row.rule.startswith(f"approval row {row.approval_row}")  # cites its source rule
    result, db = run.by_file[row.arrival], run.row_of(row.arrival)
    assert result.state == STATE[row.outcome]
    assert (result.vendor, result.invoice_number) == (row.vendor, row.number)
    assert result.precedence_row == row.approval_row
    assert row.codes <= {F(code) for code in result.finding_codes}
    if row.clean:
        assert result.finding_codes == []
    if row.paid is not None:
        assert (Decimal(db["amount_paid"]), db["currency"]) == (row.paid, "USD")
    if row.duplicate_of is not None:
        original = run.by_file[ROWS[row.duplicate_of - 1].arrival]
        assert db["duplicate_of"] == original.arrival_id
        paid = Decimal(run.db()[original.arrival_id]["amount_paid"])
        assert result.reasons == [
            f"DUPLICATE_PAYMENT: already paid {paid:.2f} USD on arrival #{original.arrival_id}"
        ]


def test_readme_scenarios_that_slice_1_covers(run):
    def codes(name):
        return {F(c) for c in run.by_file[name].finding_codes}

    for clean in ("invoice_1001.txt", "invoice_1004.json", "invoice_1006.csv"):
        assert run.by_file[clean].finding_codes == [] and run.by_file[clean].state == "paid"
    assert F.STOCK_SHORTAGE in codes("invoice_1002.txt")
    assert run.by_file["invoice_1002.txt"].decision == "needs_review"
    assert F.ITEM_ZERO_STOCK in codes("invoice_1003.txt")
    assert run.by_file["invoice_1003.txt"].decision == "rejected"
    assert F.STOCK_SHORTAGE in codes("invoice_1007.csv")
    assert run.by_file["invoice_1007.csv"].decision == "needs_review"
    assert F.ITEM_UNKNOWN in codes("invoice_1008.txt")  # 1008; 1016 below
    assert F.ITEM_UNKNOWN in codes("invoice_1016.json")
    assert {F.QUANTITY_INVALID, F.PARTIAL_IDENTITY} <= codes("invoice_1009.json")
    assert run.by_file["invoice_1009.json"].decision == "rejected"  # outranks the missing vendor


def test_duplicate_precedence_makes_no_model_call_and_no_payment(run):
    db = run.db()
    for copy in ("invoice_1011.txt", "invoice_1012.txt"):
        result = run.by_file[copy]
        decision = json.loads(db[result.arrival_id]["record"])["decision"]
        assert (result.decision, result.precedence_row, result.model_notes) == (
            "duplicate",
            1,
            "none",
        )
        assert decision["escalate_review"] is None and decision["advisory"] is None
    assert run.calls == [  # only the six Paid rows moved money, in arrival order
        ("Widgets Inc.", Decimal("5000.00"), "USD"),
        ("Precision Parts Ltd.", Decimal("1890.00"), "USD"),
        ("Acme Industrial Supplies", Decimal("2750.00"), "USD"),
        ("Summit Manufacturing Co.", Decimal("3000.00"), "USD"),
        ("QuickShip Distributers", Decimal("9975.00"), "USD"),
        ("Reliable Components Inc.", Decimal("6500.00"), "USD"),
    ]


def test_a_duplicate_carrying_a_rejection_rule_is_still_a_duplicate(run, tmp_path):
    data = json.loads((CORPUS / "invoice_1004.json").read_text())
    data["line_items"][0]["item"] = "WidgetC"  # unknown item: a Rejection Rule on any new arrival
    copy = tmp_path / "invoice_1004_copy.json"
    copy.write_text(json.dumps(data))
    result = service.process_path(copy, run.rt)
    assert (result.state, result.precedence_row) == ("duplicate", 1)
    assert F.ITEM_UNKNOWN.value in result.finding_codes  # recorded, but it does not outrank
    assert len(run.calls) == 6


def test_final_state_per_identity_and_counts_match_the_table(run):
    counts = Counter(r["state"] for r in run.db().values())
    assert counts == Counter(STATE[r.outcome] for r in ROWS_1A)
    assert counts == {"paid": 6, "needs_review": 4, "logged_rejection": 4, "duplicate": 2}
    paid_by_identity = {}
    for row in run.db().values():
        if row["state"] == "paid":
            key = (row["vendor_key"], row["invoice_number"])
            assert key not in paid_by_identity  # at most one Paid arrival per identity
            paid_by_identity[key] = Decimal(row["amount_paid"])
    expected = {(r.vendor.casefold(), r.number): r.paid for r in ROWS_1A if r.paid is not None}
    assert paid_by_identity == expected
    queue = service.review_queue(run.rt)
    assert [i.source for i in queue] == [
        "invoice_1002.txt",
        "invoice_1005.json",
        "invoice_1007.csv",
        "invoice_1010.txt",
    ]
    assert "payment_pending" not in counts


def test_offline_never_approves_a_warning_by_critic_concurrence(run):
    for row in ROWS_1A:
        result = run.by_file[row.arrival]
        if result.decision == "approved":
            assert result.finding_codes == [] and result.model_notes == "offline tier"
    warned = run.by_file["invoice_1010.txt"]
    assert F.PRICE_DEVIATION.value in warned.finding_codes and warned.state == "needs_review"
    assert warned.reasons[0].startswith("UNREVIEWED_WARNINGS")


def test_second_run_moves_no_money_and_copies_of_paid_identities_become_duplicates(run):
    first_paid = {
        (r["vendor_key"], r["invoice_number"]): (r["id"], r["amount_paid"])
        for r in run.db().values()
        if r["state"] == "paid"
    }
    again = service.run_batch(run.paths, run.rt)
    assert again.failed == [] and len(run.calls) == 6  # no payment callable invocation
    paid_files = {"invoice_1001.txt", "invoice_1004.json", "invoice_1011.pdf", "invoice_1011.txt"}
    paid_files |= {"invoice_1012.pdf", "invoice_1012.txt", "invoice_1006.csv", "invoice_1015.csv"}
    db = run.db()
    for result in again.results:
        row = db[result.arrival_id]
        if result.source in paid_files:
            assert result.state == "duplicate" and result.precedence_row == 1
            assert (row["duplicate_of"], row["amount_paid"]) == (
                first_paid[(row["vendor_key"], row["invoice_number"])][0],
                None,
            )
    assert Counter(r.state for r in again.results if r.source not in paid_files) == Counter(
        needs_review=4, logged_rejection=4
    )
    paid_now = {
        (r["vendor_key"], r["invoice_number"]): (r["id"], r["amount_paid"])
        for r in db.values()
        if r["state"] == "paid"
    }
    assert paid_now == first_paid  # every identity's paid total is unchanged


def test_the_network_guard_was_live_for_the_whole_run(run):
    with pytest.raises(AssertionError, match="network access attempted"):
        socket.socket()
    assert run.batch.failed == []


def test_pre_rate_eur_goes_to_review_with_no_rate_finding(run):
    result = service.process_path(
        CORPUS / "invoice_1014.xml", run.rt
    )  # row 18: asserted in slice 3
    assert result.state == "needs_review"
    assert {F.CURRENCY_NON_USD.value, F.CURRENCY_NO_RATE.value} <= set(result.finding_codes)
    assert len(run.calls) == 6  # no payment attempted for the unsupported currency
