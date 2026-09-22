from datetime import date

import pytest
from pydantic import ValidationError

from services.sessions_service.schemas.templates import GenerateSessionsRequest


def test_generation_request_accepts_one_exact_date():
    request = GenerateSessionsRequest(dates=[date(2026, 10, 3)])

    assert request.dates == [date(2026, 10, 3)]
    assert request.weeks is None


def test_generation_request_accepts_an_inclusive_date_range():
    request = GenerateSessionsRequest(
        from_date=date(2026, 10, 1),
        to_date=date(2026, 12, 31),
    )

    assert request.from_date == date(2026, 10, 1)
    assert request.to_date == date(2026, 12, 31)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"weeks": 4, "dates": [date(2026, 10, 3)]},
        {"from_date": date(2026, 10, 1)},
        {
            "from_date": date(2026, 10, 2),
            "to_date": date(2026, 10, 1),
        },
        {"dates": [date(2026, 10, 3), date(2026, 10, 3)]},
    ],
)
def test_generation_request_rejects_ambiguous_or_invalid_windows(payload):
    with pytest.raises(ValidationError):
        GenerateSessionsRequest(**payload)
