"""Unit tests for outreach state-machine helpers (no DB, no email)."""

from services.corporate_service.models.enums import TouchpointType
from services.corporate_service.services.outreach_templates import (
    OUTREACH_TYPES_IN_ORDER,
    next_email_number,
    render_email,
)


class TestNextEmailNumber:
    def test_no_previous_outreach_starts_at_1(self):
        assert next_email_number(None) == 1

    def test_after_intro_next_is_2(self):
        assert next_email_number(TouchpointType.EMAIL_INTRO) == 2

    def test_after_followup_1_next_is_3(self):
        assert next_email_number(TouchpointType.EMAIL_FOLLOWUP_1) == 3

    def test_after_followup_2_sequence_done(self):
        assert next_email_number(TouchpointType.EMAIL_FOLLOWUP_2) is None

    def test_non_outreach_touchpoint_treated_as_done(self):
        # If the most recent touchpoint isn't a sequence email (e.g. a
        # phone call replaced the last step), don't auto-fire another.
        assert next_email_number(TouchpointType.PHONE_CALL) is None


class TestOutreachTypesInOrder:
    def test_exactly_three_types(self):
        assert len(OUTREACH_TYPES_IN_ORDER) == 3

    def test_order_matches_playbook(self):
        assert OUTREACH_TYPES_IN_ORDER == (
            TouchpointType.EMAIL_INTRO,
            TouchpointType.EMAIL_FOLLOWUP_1,
            TouchpointType.EMAIL_FOLLOWUP_2,
        )


class TestRenderEmail:
    def test_email_1_personalises_first_name(self):
        out = render_email(1, contact_name="Jane Doe", company_name="Acme Tech")
        assert out.touchpoint_type == TouchpointType.EMAIL_INTRO
        assert "Hi Jane," in out.plain
        assert "Acme Tech" in out.plain
        assert "Acme Tech" in out.subject
        assert "https://swimbuddz.com/corporate" in out.plain

    def test_email_2_is_short_followup(self):
        out = render_email(2, contact_name="Sam", company_name="Acme")
        assert out.touchpoint_type == TouchpointType.EMAIL_FOLLOWUP_1
        assert out.subject.startswith("Re:")
        assert "Bumping this up" in out.plain

    def test_email_3_offers_redirect(self):
        out = render_email(3, contact_name="Sam", company_name="Acme")
        assert out.touchpoint_type == TouchpointType.EMAIL_FOLLOWUP_2
        assert "Last note from me" in out.plain
        assert "redirect" in out.plain

    def test_unknown_number_raises(self):
        import pytest

        with pytest.raises(ValueError):
            render_email(4, contact_name="x", company_name="y")  # type: ignore[arg-type]

    def test_missing_name_falls_back_gracefully(self):
        out = render_email(1, contact_name="", company_name="Acme")
        assert "Hello," in out.plain  # no awkward "Hi ,"
