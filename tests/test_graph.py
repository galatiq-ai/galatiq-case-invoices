"""Tests for graph routing behavior."""

from src.graph import should_reingest


def test_hold_with_hints_reingests():
    state = {
        "approval_decision": {
            "decision": "hold",
            "correction_count": 0,
            "correction_hints": ["Re-read the due date"],
        }
    }
    assert should_reingest(state) == "ingest"


def test_hold_without_hints_skips_reingest():
    state = {
        "approval_decision": {
            "decision": "hold",
            "correction_count": 0,
            "correction_hints": [],
        }
    }
    assert should_reingest(state) == "payment"


def test_hold_caps_at_two_loops():
    state = {
        "approval_decision": {
            "decision": "hold",
            "correction_count": 2,
            "correction_hints": ["Re-read the due date"],
        }
    }
    assert should_reingest(state) == "payment"
