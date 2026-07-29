from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.academy_service.schemas import CohortExtensionRequestReview
from services.payments_service.models import RecurringPayoutStatus
from services.payments_service.services.payout_calculator import block_window
from services.payments_service.services.recurring_payout_extensions import (
    extend_recurring_payout_schedules,
    extended_block_count,
)

UTC = timezone.utc
START = datetime(2026, 4, 18, tzinfo=UTC)
ORIGINAL_END = datetime(2026, 7, 11, tzinfo=UTC)
FOUR_WEEK_END = datetime(2026, 8, 8, tzinfo=UTC)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows
        self.commits = 0

    async def execute(self, _statement):
        return _Result(self.rows)

    async def commit(self):
        self.commits += 1


def _config():
    return SimpleNamespace(
        id=uuid4(),
        cohort_id=uuid4(),
        cohort_start_date=START,
        cohort_end_date=ORIGINAL_END,
        block_length_days=28,
        total_blocks=3,
        block_index=3,
        next_run_date=datetime(2026, 8, 2, tzinfo=UTC),
        status=RecurringPayoutStatus.COMPLETED,
        total_classes=12,
        per_class_amount_kobo=500_000,
        notes=None,
    )


def test_admin_review_defaults_extension_coach_payout_to_off():
    review = CohortExtensionRequestReview()
    assert review.coach_payout_billable is False


def test_admin_can_explicitly_make_extension_coach_payout_billable():
    review = CohortExtensionRequestReview(coach_payout_billable=True)
    assert review.coach_payout_billable is True


def test_four_week_extension_adds_exactly_one_block():
    assert (
        extended_block_count(
            cohort_start=START,
            proposed_end=FOUR_WEEK_END,
            block_length_days=28,
        )
        == 4
    )


@pytest.mark.asyncio
async def test_billable_extension_reactivates_completed_payout_schedule():
    config = _config()
    db = _FakeDb([config])

    schedules = await extend_recurring_payout_schedules(
        db,
        cohort_id=config.cohort_id,
        current_end=ORIGINAL_END,
        proposed_end=FOUR_WEEK_END,
    )

    assert db.commits == 1
    assert config.total_blocks == 4
    assert config.cohort_end_date == FOUR_WEEK_END
    assert config.status == RecurringPayoutStatus.ACTIVE
    assert config.next_run_date == datetime(2026, 8, 9, tzinfo=UTC)
    assert schedules[0].changed is True
    assert schedules[0].previous_total_blocks == 3


@pytest.mark.asyncio
async def test_extension_schedule_sync_is_idempotent():
    config = _config()
    config.cohort_end_date = FOUR_WEEK_END
    config.total_blocks = 4
    config.block_index = 4
    config.status = RecurringPayoutStatus.COMPLETED
    db = _FakeDb([config])

    schedules = await extend_recurring_payout_schedules(
        db,
        cohort_id=config.cohort_id,
        current_end=ORIGINAL_END,
        proposed_end=FOUR_WEEK_END,
    )

    assert schedules[0].changed is False
    assert config.total_blocks == 4


@pytest.mark.asyncio
async def test_existing_extended_snapshot_is_repaired_if_block_is_missing():
    config = _config()
    config.cohort_end_date = FOUR_WEEK_END
    db = _FakeDb([config])

    schedules = await extend_recurring_payout_schedules(
        db,
        cohort_id=config.cohort_id,
        current_end=ORIGINAL_END,
        proposed_end=FOUR_WEEK_END,
    )

    assert schedules[0].changed is True
    assert config.total_blocks == 4
    assert config.status == RecurringPayoutStatus.ACTIVE


def test_partial_extension_clamps_the_final_payout_window():
    config = _config()
    partial_end = datetime(2026, 7, 18, tzinfo=UTC)
    config.cohort_end_date = partial_end
    config.total_blocks = 4

    start, end = block_window(config, 3)

    assert start == ORIGINAL_END
    assert end == datetime(2026, 7, 19, tzinfo=UTC)


def test_final_block_includes_a_class_on_the_cohort_end_date():
    config = _config()
    config.cohort_end_date = FOUR_WEEK_END
    config.total_blocks = 4

    start, end = block_window(config, 3)

    assert start == ORIGINAL_END
    assert end == datetime(2026, 8, 9, tzinfo=UTC)
