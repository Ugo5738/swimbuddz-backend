"""Reusable async HTTP client for internal service-to-service communication.

All cross-service calls should go through this package instead of importing
models or querying tables from other services directly.

This module is a back-compat facade: it re-exports every public symbol from
the per-service submodules so existing
``from libs.common.service_client import X`` imports keep working unchanged.
New code may import directly from a submodule
(``from libs.common.service_client.wallet import debit_member_wallet``).
"""

from __future__ import annotations

from .academy import check_cohort_enrollment, list_enrollment_progress
from .attendance import get_member_attendance
from .communications import dispatch_notification
from .core import (
    _DEFAULT_TIMEOUT,
    internal_delete,
    internal_get,
    internal_patch,
    internal_post,
    internal_request,
)
from .members import (
    get_admin_members,
    get_birthdays_today,
    get_coach_availability,
    get_coach_profile,
    get_coach_readiness_data,
    get_eligible_coaches,
    get_member_by_auth_id,
    get_member_by_id,
    get_member_membership,
    get_members_bulk,
    get_pod_by_id,
    list_pods,
    search_members,
)
from .payments import (
    PaystackProxyError,
    _proxy_error_from,
    complete_makeup_obligation,
    initialize_store_payment,
    paystack_create_recipient,
    paystack_list_banks,
    paystack_resolve_account,
    schedule_makeup_obligation,
    validate_discount_code,
    verify_store_payment,
)
from .sessions import (
    generate_cohort_sessions,
    get_booking_by_id,
    get_completed_session_ids_for_cohort,
    get_next_session_for_cohort,
    get_session_by_id,
    get_session_ids_for_cohort,
)
from .volunteer import (
    cancel_opportunities_for_context,
    grant_challenge_volunteer_hours,
    materialise_opportunities_from_session_template,
)
from .wallet import (
    check_wallet_balance,
    credit_member_wallet,
    debit_member_wallet,
    emit_rewards_event,
    get_wallet_balance,
    grant_challenge_reward_bubbles,
    grant_pool_submission_reward,
)

__all__ = [
    # Core
    "_DEFAULT_TIMEOUT",
    "internal_request",
    "internal_get",
    "internal_post",
    "internal_patch",
    "internal_delete",
    # Members
    "get_member_by_auth_id",
    "search_members",
    "get_member_by_id",
    "get_members_bulk",
    "get_coach_availability",
    "get_coach_profile",
    "get_member_membership",
    "get_coach_readiness_data",
    "get_birthdays_today",
    "get_admin_members",
    "get_eligible_coaches",
    "get_pod_by_id",
    "list_pods",
    # Academy
    "check_cohort_enrollment",
    "list_enrollment_progress",
    # Attendance
    "get_member_attendance",
    # Sessions
    "get_booking_by_id",
    "generate_cohort_sessions",
    "get_completed_session_ids_for_cohort",
    "get_session_by_id",
    "get_next_session_for_cohort",
    "get_session_ids_for_cohort",
    # Wallet
    "get_wallet_balance",
    "grant_pool_submission_reward",
    "grant_challenge_reward_bubbles",
    "debit_member_wallet",
    "credit_member_wallet",
    "check_wallet_balance",
    "emit_rewards_event",
    # Volunteer
    "grant_challenge_volunteer_hours",
    "cancel_opportunities_for_context",
    "materialise_opportunities_from_session_template",
    # Payments / Paystack
    "initialize_store_payment",
    "verify_store_payment",
    "validate_discount_code",
    "schedule_makeup_obligation",
    "complete_makeup_obligation",
    "PaystackProxyError",
    "_proxy_error_from",
    "paystack_list_banks",
    "paystack_resolve_account",
    "paystack_create_recipient",
    # Communications
    "dispatch_notification",
]
