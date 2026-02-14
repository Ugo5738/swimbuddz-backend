"""
Model factories for creating valid test data.

Every factory produces a valid, insertable SQLAlchemy model instance.
Override any field via kwargs.

Usage:
    member = MemberFactory.create(email="custom@test.com")
    db_session.add(member)
    await db_session.commit()
"""

import json
import uuid
from datetime import date, datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tomorrow() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=1)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:8]}@test.com"


# ---------------------------------------------------------------------------
# Members Service
# ---------------------------------------------------------------------------


class MemberFactory:
    @staticmethod
    def create(**overrides):
        from services.members_service.models import Member

        defaults = {
            "id": _uuid(),
            "auth_id": str(_uuid()),
            "email": _unique_email(),
            "first_name": "Test",
            "last_name": "Member",
            "is_active": True,
            "registration_complete": True,
            "roles": ["member"],
            "approval_status": "approved",
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Member(**defaults)


class CoachProfileFactory:
    @staticmethod
    def create(member_id=None, **overrides):
        from services.members_service.models import CoachProfile

        defaults = {
            "id": _uuid(),
            "member_id": member_id or _uuid(),
            "display_name": "Test Coach",
            "status": "approved",
            "learn_to_swim_grade": "grade_2",
            "special_populations_grade": None,
            "institutional_grade": None,
            "competitive_elite_grade": None,
            "total_coaching_hours": 100,
            "cohorts_completed": 5,
            "average_feedback_rating": 4.5,
            "coaching_years": 3,
            "max_swimmers_per_session": 10,
            "max_cohorts_at_once": 2,
            "currency": "NGN",
            "show_in_directory": True,
            "is_featured": False,
            "is_verified": True,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return CoachProfile(**defaults)


class PendingRegistrationFactory:
    @staticmethod
    def create(**overrides):
        from services.members_service.models import PendingRegistration

        email = overrides.pop("email", _unique_email())
        defaults = {
            "email": email,
            "profile_data_json": json.dumps(
                {
                    "first_name": "Pending",
                    "last_name": "User",
                    "email": email,
                }
            ),
        }
        defaults.update(overrides)
        return PendingRegistration(**defaults)


class CoachBankAccountFactory:
    @staticmethod
    def create(member_id=None, **overrides):
        from services.members_service.models import CoachBankAccount

        defaults = {
            "id": _uuid(),
            "member_id": member_id or _uuid(),
            "bank_code": "058",
            "bank_name": "GTBank",
            "account_number": "0123456789",
            "account_name": "Test Coach",
            "is_verified": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return CoachBankAccount(**defaults)


# ---------------------------------------------------------------------------
# Sessions Service
# ---------------------------------------------------------------------------


class SessionFactory:
    @staticmethod
    def create(**overrides):
        from services.sessions_service.models import (
            Session,
            SessionLocation,
            SessionStatus,
            SessionType,
        )

        # Convert string values to enums — accept both NAME (SCHEDULED)
        # and value (scheduled) forms for convenience
        def _to_enum(val, enum_cls):
            if isinstance(val, str):
                # Try by name first (e.g. "SCHEDULED"), then by value (e.g. "scheduled")
                try:
                    return enum_cls[val]
                except KeyError:
                    return enum_cls(val)
            return val

        if "session_type" in overrides:
            overrides["session_type"] = _to_enum(overrides["session_type"], SessionType)
        if "status" in overrides:
            overrides["status"] = _to_enum(overrides["status"], SessionStatus)
        if "location" in overrides:
            overrides["location"] = _to_enum(overrides["location"], SessionLocation)

        tomorrow = _tomorrow()
        defaults = {
            "id": _uuid(),
            "title": "Test Session",
            "session_type": SessionType.CLUB,
            "status": SessionStatus.SCHEDULED,
            "starts_at": tomorrow,
            "ends_at": tomorrow + timedelta(hours=2),
            "timezone": "Africa/Lagos",
            "location": SessionLocation.SUNFIT_POOL,
            "capacity": 20,
            "pool_fee": 2000.0,
            "ride_share_fee": 0.0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Session(**defaults)


class SessionCoachFactory:
    @staticmethod
    def create(session_id=None, coach_id=None, **overrides):
        from services.sessions_service.models import SessionCoach

        defaults = {
            "id": _uuid(),
            "session_id": session_id or _uuid(),
            "coach_id": coach_id or _uuid(),
            "role": "lead",
            "created_at": _now(),
        }
        defaults.update(overrides)
        return SessionCoach(**defaults)


# ---------------------------------------------------------------------------
# Academy Service
# ---------------------------------------------------------------------------


class ProgramFactory:
    @staticmethod
    def create(**overrides):
        from services.academy_service.models import BillingType, Program, ProgramLevel

        defaults = {
            "id": _uuid(),
            "name": "Beginner Swim Program",
            "slug": f"beginner-{uuid.uuid4().hex[:6]}",
            "description": "A 12-week program for adult beginners.",
            "level": ProgramLevel.BEGINNER_1,
            "duration_weeks": 12,
            "default_capacity": 10,
            "currency": "NGN",
            "price_amount": 150000,
            "billing_type": BillingType.ONE_TIME,
            "is_published": True,
            "version": 1,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Program(**defaults)


class CohortFactory:
    @staticmethod
    def create(program_id=None, **overrides):
        from services.academy_service.models import Cohort, CohortStatus, LocationType

        start = _tomorrow()
        defaults = {
            "id": _uuid(),
            "program_id": program_id or _uuid(),
            "name": f"Cohort Q1-{uuid.uuid4().hex[:4]}",
            "start_date": start,
            "end_date": start + timedelta(weeks=12),
            "capacity": 20,
            "timezone": "Africa/Lagos",
            "location_type": LocationType.POOL,
            "location_name": "Sunfit Pool",
            "status": CohortStatus.OPEN,
            "allow_mid_entry": False,
            "mid_entry_cutoff_week": 2,
            "require_approval": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Cohort(**defaults)


class EnrollmentFactory:
    @staticmethod
    def create(cohort_id=None, member_id=None, **overrides):
        from services.academy_service.models import (
            Enrollment,
            EnrollmentSource,
            EnrollmentStatus,
            PaymentStatus,
        )

        # Convert string overrides to enum values
        _status_map = {e.name: e for e in EnrollmentStatus}
        if "status" in overrides and isinstance(overrides["status"], str):
            overrides["status"] = _status_map.get(
                overrides["status"]
            ) or EnrollmentStatus(overrides["status"])

        defaults = {
            "id": _uuid(),
            "cohort_id": cohort_id or _uuid(),
            "member_id": member_id or _uuid(),
            "member_auth_id": str(_uuid()),
            "status": EnrollmentStatus.ENROLLED,
            "payment_status": PaymentStatus.PAID,
            "price_snapshot_amount": 150000,
            "currency_snapshot": "NGN",
            "source": EnrollmentSource.WEB,
            "enrolled_at": _now(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Enrollment(**defaults)


class MilestoneFactory:
    @staticmethod
    def create(program_id=None, **overrides):
        from services.academy_service.models import (
            Milestone,
            MilestoneType,
            RequiredEvidence,
        )

        defaults = {
            "id": _uuid(),
            "program_id": program_id or _uuid(),
            "name": "Float on back for 10 seconds",
            "criteria": "Student can float on back unassisted for at least 10 seconds",
            "order_index": 0,
            "milestone_type": MilestoneType.SKILL,
            "required_evidence": RequiredEvidence.NONE,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Milestone(**defaults)


class CoachAssignmentFactory:
    @staticmethod
    def create(cohort_id=None, coach_id=None, assigned_by_id=None, **overrides):
        from services.academy_service.models import CoachAssignment

        defaults = {
            "id": _uuid(),
            "cohort_id": cohort_id or _uuid(),
            "coach_id": coach_id or _uuid(),
            "role": "lead",
            "start_date": _now(),
            "assigned_by_id": assigned_by_id or _uuid(),
            "status": "active",
            "is_session_override": False,
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return CoachAssignment(**defaults)


# ---------------------------------------------------------------------------
# Attendance Service
# ---------------------------------------------------------------------------


class AttendanceRecordFactory:
    @staticmethod
    def create(session_id=None, member_id=None, **overrides):
        from services.attendance_service.models import AttendanceRecord

        defaults = {
            "id": _uuid(),
            "session_id": session_id or _uuid(),
            "member_id": member_id or _uuid(),
            "status": "PRESENT",
            "role": "SWIMMER",
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return AttendanceRecord(**defaults)


# ---------------------------------------------------------------------------
# Payments Service
# ---------------------------------------------------------------------------


class PaymentFactory:
    @staticmethod
    def create(**overrides):
        from services.payments_service.models import Payment

        defaults = {
            "id": _uuid(),
            "reference": f"PAY-{uuid.uuid4().hex[:5].upper()}",
            "member_auth_id": str(_uuid()),
            "payer_email": _unique_email(),
            "purpose": "COMMUNITY",
            "amount": 20000.0,
            "currency": "NGN",
            "status": "PENDING",
            "payment_method": "paystack",
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Payment(**defaults)


class DiscountFactory:
    @staticmethod
    def create(**overrides):
        from services.payments_service.models import Discount

        defaults = {
            "id": _uuid(),
            "code": f"TEST-{uuid.uuid4().hex[:6].upper()}",
            "description": "Test discount",
            "discount_type": "PERCENTAGE",
            "value": 10.0,
            "is_active": True,
            "current_uses": 0,
            "max_uses": None,
            "valid_from": _now() - timedelta(days=1),
            "valid_until": _now() + timedelta(days=30),
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Discount(**defaults)


class CoachPayoutFactory:
    @staticmethod
    def create(coach_member_id=None, **overrides):
        from services.payments_service.models import CoachPayout

        defaults = {
            "id": _uuid(),
            "coach_member_id": coach_member_id or _uuid(),
            "period_start": _now() - timedelta(days=30),
            "period_end": _now(),
            "period_label": "January 2026",
            "academy_earnings": 50000,
            "session_earnings": 30000,
            "other_earnings": 0,
            "total_amount": 80000,
            "currency": "NGN",
            "status": "PENDING",
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return CoachPayout(**defaults)


# ---------------------------------------------------------------------------
# Communications Service
# ---------------------------------------------------------------------------


class AnnouncementFactory:
    @staticmethod
    def create(**overrides):
        from services.communications_service.models import (
            Announcement,
            AnnouncementAudience,
            AnnouncementCategory,
            AnnouncementStatus,
        )

        defaults = {
            "id": _uuid(),
            "title": "Test Announcement",
            "summary": "A test summary",
            "body": "This is the full body of the test announcement.",
            "category": AnnouncementCategory.GENERAL,
            "status": AnnouncementStatus.PUBLISHED,
            "audience": AnnouncementAudience.COMMUNITY,
            "notify_email": False,
            "notify_push": False,
            "is_pinned": False,
            "published_at": _now(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return Announcement(**defaults)


class ContentPostFactory:
    @staticmethod
    def create(created_by=None, **overrides):
        from services.communications_service.models import ContentPost

        defaults = {
            "id": _uuid(),
            "title": "Test Content Post",
            "summary": "A test content summary",
            "body": "# Test Post\n\nThis is a test content post.",
            "category": "swimming_tips",
            "is_published": True,
            "tier_access": "community",
            "published_at": _now(),
            "created_by": created_by or _uuid(),
            "created_at": _now(),
            "updated_at": _now(),
        }
        defaults.update(overrides)
        return ContentPost(**defaults)
