"""Challenges helpers package.

Split from the 918-line `_helpers.py` per docs/CONVENTIONS.md §12 into
six themed modules:

  _auth.py          identity, role, prerequisite, review authorization
  _members.py       bulk member-record loading + name formatting
  _media.py         media + roster loaders
  _responses.py     response builders (admin / public / submission)
  _rewards.py       post-approval badge ledger + external grants
  _notifications.py submission lifecycle notifications

Public import surface preserved: route modules import every name from
`._helpers` (the package); this `__init__.py` re-exports them.
"""

from libs.common.logging import get_logger

from ._auth import (
    _admin_uuid_or_none,
    _authorize_review,
    _enforce_prerequisite,
    _pod_lead_kind_for_member,
    _resolve_member_id_from_auth,
    _resolve_member_id_from_auth_optional,
)
from ._media import (
    _load_challenge_example_media,
    _load_submission_members,
    _load_submission_proof_media,
)
from ._members import (
    _full_name,
    _load_member_names,
    _load_member_records,
    _short_display_name,
)
from ._notifications import (
    _notify_submission_reviewed,
    _notify_submission_winner,
)
from ._responses import (
    _build_winner_info,
    _hydrate_challenge_response,
    _hydrate_public_challenge_response,
    _hydrate_submission_response,
)
from ._rewards import (
    _award_badge_and_members,
    _distribute_external_rewards,
)

# Public constants + module logger used by route modules; matches the
# pre-split surface so route files keep their existing
# `from ._helpers import (CHALLENGES_CALLING_SERVICE, logger, ...)`.
CHALLENGES_CALLING_SERVICE = "members_service.challenges"
logger = get_logger("services.members_service.routers.challenges._helpers")

__all__ = [
    "CHALLENGES_CALLING_SERVICE",
    "logger",
    # _auth
    "_admin_uuid_or_none",
    "_authorize_review",
    "_enforce_prerequisite",
    "_pod_lead_kind_for_member",
    "_resolve_member_id_from_auth",
    "_resolve_member_id_from_auth_optional",
    # _members
    "_full_name",
    "_load_member_names",
    "_load_member_records",
    "_short_display_name",
    # _media
    "_load_challenge_example_media",
    "_load_submission_members",
    "_load_submission_proof_media",
    # _responses
    "_build_winner_info",
    "_hydrate_challenge_response",
    "_hydrate_public_challenge_response",
    "_hydrate_submission_response",
    # _rewards
    "_award_badge_and_members",
    "_distribute_external_rewards",
    # _notifications
    "_notify_submission_reviewed",
    "_notify_submission_winner",
]
