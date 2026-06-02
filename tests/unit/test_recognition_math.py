"""Unit tests for the pure revenue-recognition math (design §10). No DB."""

from datetime import date

from services.ledger_service.services.recognition import recognizable_amount

START = date(2026, 1, 1)
END = date(2026, 1, 31)  # 30-day span
TOTAL = 30_000  # kobo


def test_before_start_recognises_nothing():
    assert recognizable_amount(TOTAL, 0, START, END, date(2025, 12, 31)) == 0


def test_at_start_recognises_nothing():
    assert recognizable_amount(TOTAL, 0, START, END, START) == 0


def test_midpoint_recognises_half():
    # 15 of 30 days elapsed -> half.
    assert recognizable_amount(TOTAL, 0, START, END, date(2026, 1, 16)) == 15_000


def test_after_end_recognises_full():
    assert recognizable_amount(TOTAL, 0, START, END, date(2026, 2, 15)) == TOTAL


def test_already_fully_recognised_returns_zero():
    assert recognizable_amount(TOTAL, TOTAL, START, END, date(2026, 2, 15)) == 0


def test_subtracts_already_recognised():
    # Half earned (15_000), 5_000 already recognised -> 10_000 delta.
    assert recognizable_amount(TOTAL, 5_000, START, END, date(2026, 1, 16)) == 10_000


def test_never_negative_when_over_recognised():
    assert recognizable_amount(TOTAL, TOTAL + 1, START, END, START) == 0


def test_zero_duration_recognises_full():
    # start == end (degenerate) must not divide by zero; recognises in full.
    assert recognizable_amount(TOTAL, 0, START, START, START) == TOTAL


def test_no_drift_recognises_exactly_total_by_end():
    # Incremental recognition over the period must sum to TOTAL exactly — the
    # final step (as_of >= end) clears any integer-division remainder.
    recognized = 0
    for d in (date(2026, 1, 8), date(2026, 1, 16), date(2026, 1, 24), END):
        recognized += recognizable_amount(TOTAL, recognized, START, END, d)
    assert recognized == TOTAL


def test_odd_amount_no_drift():
    # An amount that doesn't divide evenly still lands exactly on total by end.
    total = 100_001
    recognized = 0
    for day in range(1, 32):
        recognized += recognizable_amount(
            total, recognized, START, END, date(2026, 1, day)
        )
    assert recognized == total
