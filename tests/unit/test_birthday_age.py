"""Unit tests for the birthday-age helper used by the daily birthday cron."""

from datetime import date, datetime, timezone

from services.members_service.routers.internal import _age_on


class TestAgeOn:
    def test_age_on_exact_birthday(self):
        """Age increments on the day-of birthday."""
        dob = datetime(1990, 5, 10, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 5, 10)) == 36

    def test_age_on_day_before_birthday(self):
        """Day before birthday returns age - 1."""
        dob = datetime(1990, 5, 10, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 5, 9)) == 35

    def test_age_on_day_after_birthday(self):
        """Day after birthday: age has incremented."""
        dob = datetime(1990, 5, 10, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 5, 11)) == 36

    def test_age_in_earlier_month_same_year(self):
        """Earlier month within the same year: not yet aged."""
        dob = datetime(1990, 12, 1, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 6, 1)) == 35

    def test_age_in_later_month_same_year(self):
        """Later month within the same year: already aged."""
        dob = datetime(1990, 1, 1, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 6, 1)) == 36

    def test_leap_year_birthday_on_non_leap_year(self):
        """Feb 29 baby on a non-leap-year Feb 28 has not yet hit birthday.

        We don't move the milestone to Feb 28; that's a product decision
        and it's safer to under-count by one day than to send a duplicate
        birthday email next year.
        """
        dob = datetime(2000, 2, 29, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 2, 28)) == 25

    def test_leap_year_birthday_on_march_first_non_leap_year(self):
        """Feb 29 baby is treated as 'aged' from March 1 in non-leap years."""
        dob = datetime(2000, 2, 29, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 3, 1)) == 26

    def test_newborn_today(self):
        """Born today returns age 0."""
        dob = datetime(2026, 5, 10, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 5, 10)) == 0

    def test_future_dob_clamps_to_zero(self):
        """A DOB in the future (data entry error) doesn't go negative."""
        dob = datetime(2030, 1, 1, tzinfo=timezone.utc)
        assert _age_on(dob, date(2026, 1, 1)) == 0

    def test_accepts_naive_date(self):
        """Helper handles plain `date` (not just `datetime`)."""
        dob = date(1990, 5, 10)
        assert _age_on(dob, date(2026, 5, 10)) == 36
