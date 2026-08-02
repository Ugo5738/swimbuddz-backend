"""Tests for event recurrence and controlled workbook parsing."""

import uuid
from datetime import date, time
from io import BytesIO

from openpyxl import Workbook

from services.events_service.models import EventTemplate
from services.events_service.services.calendar_import import parse_calendar_import
from services.events_service.services.recurrence import build_occurrences


def _template(**overrides) -> EventTemplate:
    values = {
        "id": uuid.uuid4(),
        "title": "Intro-to-Water Assessment",
        "event_type": "assessment",
        "audience": "academy",
        "visibility": "public",
        "location_type": "physical",
        "timezone": "Africa/Lagos",
        "local_start_time": time(9, 0),
        "duration_minutes": 120,
        "tier_access": "public",
        "frequency": "monthly",
        "interval": 1,
        "day_of_week": 6,
        "week_of_month": 2,
        "starts_on": date(2027, 1, 1),
        "is_active": True,
        "created_by": uuid.uuid4(),
    }
    values.update(overrides)
    return EventTemplate(**values)


def test_monthly_second_sunday_occurrences() -> None:
    occurrences = build_occurrences(
        _template(),
        date(2027, 1, 1),
        date(2027, 3, 31),
    )

    assert [item.local_date.isoformat() for item in occurrences] == [
        "2027-01-10",
        "2027-02-14",
        "2027-03-14",
    ]
    assert all(
        item.start_time.utcoffset().total_seconds() == 3600 for item in occurrences
    )


def test_quarterly_last_saturday_occurrences() -> None:
    template = _template(
        frequency="quarterly",
        day_of_week=5,
        week_of_month=-1,
        starts_on=date(2027, 1, 1),
    )

    occurrences = build_occurrences(
        template,
        date(2027, 1, 1),
        date(2027, 12, 31),
    )

    assert [item.local_date.isoformat() for item in occurrences] == [
        "2027-01-30",
        "2027-04-24",
        "2027-07-31",
        "2027-10-30",
    ]


def test_calendar_import_parses_controlled_sheet_as_drafts() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Calendar Import"
    headers = [
        "Import",
        "Start Date",
        "Start Time",
        "End Date",
        "End Time",
        "Title",
        "Description",
        "Audience",
        "Visibility",
        "Tier Access",
        "Event Type",
        "Location Type",
        "Location",
        "Location Area",
        "Timezone",
        "Status",
        "Source Sheet",
        "Source Row",
        "External Key",
    ]
    sheet.append(headers)
    sheet.append(
        [
            "Yes",
            date(2027, 2, 14),
            time(9, 0),
            date(2027, 2, 14),
            time(11, 0),
            "Free Intro-to-Water Assessment",
            "Diagnostic and placement clinic.",
            "Academy",
            "Public",
            "All",
            "Assessment",
            "Physical",
            "TBC - confirm before publishing",
            "TBC",
            "Africa/Lagos",
            "Published",
            "Community",
            46,
            "community-2027-02-14-assessment",
        ]
    )
    output = BytesIO()
    workbook.save(output)

    preview = parse_calendar_import(output.getvalue())

    assert preview.valid_count == 1
    assert preview.invalid_count == 0
    assert preview.rows[0].event is not None
    assert preview.rows[0].event.status == "draft"
    assert preview.rows[0].event.audience == "academy"
    assert preview.rows[0].event.tier_access == "public"
    assert preview.rows[0].warnings == [
        "Confirm the venue or meeting link before publishing."
    ]


def test_calendar_import_reports_row_errors_without_aborting_preview() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Calendar Import"
    sheet.append(
        [
            "Import",
            "Start Date",
            "Start Time",
            "End Date",
            "End Time",
            "Title",
            "Audience",
            "Visibility",
            "Tier Access",
            "Event Type",
            "Location Type",
            "Timezone",
            "External Key",
        ]
    )
    sheet.append(
        [
            "Yes",
            "not-a-date",
            "09:00",
            "not-a-date",
            "11:00",
            "Assessment",
            "Academy",
            "Public",
            "All",
            "Assessment",
            "Physical",
            "Africa/Lagos",
            "bad-row",
        ]
    )
    output = BytesIO()
    workbook.save(output)

    preview = parse_calendar_import(output.getvalue())

    assert preview.valid_count == 0
    assert preview.invalid_count == 1
    assert preview.rows[0].event is None
    assert preview.rows[0].errors == ["Use a valid Excel date"]


def test_calendar_import_rejects_invalid_xlsx_container() -> None:
    try:
        parse_calendar_import(b"not an xlsx file")
    except ValueError as exc:
        assert str(exc) == "The uploaded file is not a valid .xlsx workbook"
    else:
        raise AssertionError("Invalid workbook should have been rejected")
