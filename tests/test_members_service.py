"""Unit tests for members service layer.

These tests verify the pure business logic functions without database dependencies.
"""
import pytest
from datetime import datetime, timedelta, timezone
from services.members_service import service as member_service


class TestNormalizeMemberTiers:
    """Tests for tier normalization logic."""

    def test_defaults_to_community_when_no_tiers(self):
        """Members with no tiers should default to community."""
        primary, tiers, changed = member_service.normalize_member_tiers(
            current_tier=None,
            current_tiers=None,
            community_paid_until=None,
            club_paid_until=None,
        )
        assert primary == "community"
        assert tiers == ["community"]
        assert changed is True

    def test_preserves_existing_community_tier(self):
        """Members already on community should not trigger change."""
        primary, tiers, changed = member_service.normalize_member_tiers(
            current_tier="community",
            current_tiers=["community"],
            community_paid_until=None,
            club_paid_until=None,
        )
        assert primary == "community"
        assert tiers == ["community"]
        assert changed is False

    def test_club_entitlement_adds_club_and_community(self):
        """Active club subscription should add both club and community tiers."""
        future = datetime.now(timezone.utc) + timedelta(days=30)
        primary, tiers, changed = member_service.normalize_member_tiers(
            current_tier="community",
            current_tiers=["community"],
            community_paid_until=future,
            club_paid_until=future,
        )
        assert primary == "club"
        assert "club" in tiers
        assert "community" in tiers
        assert changed is True

    def test_expired_club_keeps_existing_tiers(self):
        """Expired club subscription should keep existing tiers (additive model).
        
        Note: The tier normalization is additive - it doesn't remove tiers when
        payments expire. This is intentional to avoid accidental tier loss.
        Tier removal should be done through explicit admin action.
        """
        past = datetime.now(timezone.utc) - timedelta(days=1)
        future = datetime.now(timezone.utc) + timedelta(days=30)
        primary, tiers, changed = member_service.normalize_member_tiers(
            current_tier="club",
            current_tiers=["club", "community"],
            community_paid_until=future,
            club_paid_until=past,  # Expired - but doesn't remove club from existing tiers
        )
        # Club is kept because it's in current_tiers (additive model)
        assert "club" in tiers
        assert "community" in tiers
        # Primary stays club since it's the highest in the set
        assert primary == "club"

    def test_tiers_sorted_by_priority(self):
        """Tiers should be sorted by priority (academy > club > community)."""
        future = datetime.now(timezone.utc) + timedelta(days=30)
        primary, tiers, _ = member_service.normalize_member_tiers(
            current_tier="community",
            current_tiers=["community", "academy", "club"],
            community_paid_until=future,
            club_paid_until=future,
        )
        assert tiers == ["academy", "club", "community"]
        assert primary == "academy"


class TestCalculateExpiry:
    """Tests for expiry date calculations."""

    def test_community_expiry_from_now(self):
        """New subscription should start from now."""
        result = member_service.calculate_community_expiry(
            current_expiry=None,
            years=1,
        )
        expected = datetime.now(timezone.utc) + timedelta(days=365)
        # Allow 2 second tolerance for test execution time
        assert abs((result - expected).total_seconds()) < 2

    def test_community_expiry_extends_active(self):
        """Active subscription should extend from current expiry."""
        current = datetime.now(timezone.utc) + timedelta(days=30)
        result = member_service.calculate_community_expiry(
            current_expiry=current,
            years=1,
        )
        expected = current + timedelta(days=365)
        assert abs((result - expected).total_seconds()) < 2

    def test_club_expiry_from_now(self):
        """New club subscription should start from now."""
        result = member_service.calculate_club_expiry(
            current_expiry=None,
            months=3,
        )
        expected = datetime.now(timezone.utc) + timedelta(days=90)
        assert abs((result - expected).total_seconds()) < 2


class TestClubReadinessValidation:
    """Tests for club readiness checks."""

    def test_complete_readiness_returns_true(self):
        """All required fields filled should return True."""
        result = member_service.validate_club_readiness(
            emergency_contact_name="John Doe",
            emergency_contact_relationship="Spouse",
            emergency_contact_phone="+234123456789",
            location_preference=["lagos"],
            time_of_day_availability=["morning"],
            availability_slots=["monday"],
        )
        assert result is True

    def test_missing_emergency_contact_returns_false(self):
        """Missing emergency contact name should return False."""
        result = member_service.validate_club_readiness(
            emergency_contact_name=None,
            emergency_contact_relationship="Spouse",
            emergency_contact_phone="+234123456789",
            location_preference=["lagos"],
            time_of_day_availability=["morning"],
            availability_slots=["monday"],
        )
        assert result is False

    def test_empty_location_preference_returns_false(self):
        """Empty location preference list should return False."""
        result = member_service.validate_club_readiness(
            emergency_contact_name="John Doe",
            emergency_contact_relationship="Spouse",
            emergency_contact_phone="+234123456789",
            location_preference=[],  # Empty
            time_of_day_availability=["morning"],
            availability_slots=["monday"],
        )
        assert result is False

    def test_missing_availability_slots_returns_false(self):
        """None availability slots should return False."""
        result = member_service.validate_club_readiness(
            emergency_contact_name="John Doe",
            emergency_contact_relationship="Spouse",
            emergency_contact_phone="+234123456789",
            location_preference=["lagos"],
            time_of_day_availability=["morning"],
            availability_slots=None,
        )
        assert result is False


class TestClubEligibility:
    """Tests for club activation eligibility."""

    def test_already_approved_is_eligible(self):
        """Member with club already approved should be eligible."""
        eligible, error = member_service.check_club_eligibility(
            approved_tiers={"club", "community"},
            requested_tiers=set(),
            readiness_complete=False,  # Shouldn't matter
        )
        assert eligible is True
        assert error is None

    def test_academy_approved_counts_as_club_eligible(self):
        """Member with academy approved should be eligible for club."""
        eligible, error = member_service.check_club_eligibility(
            approved_tiers={"academy", "community"},
            requested_tiers=set(),
            readiness_complete=False,
        )
        assert eligible is True
        assert error is None

    def test_not_requested_is_ineligible(self):
        """Member who hasn't requested club should be ineligible."""
        eligible, error = member_service.check_club_eligibility(
            approved_tiers={"community"},
            requested_tiers=set(),
            readiness_complete=True,
        )
        assert eligible is False
        assert "not requested" in error.lower()

    def test_requested_but_incomplete_readiness(self):
        """Member who requested but didn't complete readiness should be ineligible."""
        eligible, error = member_service.check_club_eligibility(
            approved_tiers={"community"},
            requested_tiers={"club"},
            readiness_complete=False,
        )
        assert eligible is False
        assert "incomplete" in error.lower()

    def test_requested_with_complete_readiness_is_eligible(self):
        """Member who requested and completed readiness should be eligible."""
        eligible, error = member_service.check_club_eligibility(
            approved_tiers={"community"},
            requested_tiers={"club"},
            readiness_complete=True,
        )
        assert eligible is True
        assert error is None
