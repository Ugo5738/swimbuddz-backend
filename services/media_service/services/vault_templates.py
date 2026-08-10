"""Default operating standard for session media vaults.

The concise checklist is intentionally taken from the session-day page of the
SwimBuddz Club Session Media Coverage Guide.  The full guide remains the
onboarding reference; this module keeps the minimum standard in the workflow
where a volunteer actually uploads the files.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_MEDIA_VAULT_CHECKLIST = [
    "Establishing shot and preparation or arrival",
    "Warm-up wide shot and two drill close-ups",
    "Side-angle footage showing complete movement",
    "Coaching sequence: instruction, attempt, correction, improved attempt",
    "Two members completing meaningful parts of the main set",
    "One uninterrupted complete-length swim",
    "Cool-down or coach, peer, or pod review",
    "Progress, reaction, or encouragement moment",
    "Group or pod photo and a candid community moment",
    "At least two useful horizontal clips for the website or YouTube",
]

DEFAULT_MEDIA_VAULT_CONSENT_NOTICE = (
    "Confirm member media preferences before filming. Do not upload changing-area, "
    "private, unsafe, humiliating, or uncertain-consent footage, and flag every "
    "safeguarding concern in the handoff notes."
)

_DEFAULT_MEDIA_COVERAGE_SETTINGS: dict[str, Any] = {
    "coverage_standard": "club-session-media-v1",
    "story": ["prepare", "practise", "coach", "progress", "belong"],
    "upload_deadline_hours": 24,
    "output_targets": {
        "vertical_video": "12-18 usable clips",
        "horizontal_video": "2-4 useful clips",
        "photographs": "6-10 strong images",
        "complete_length_swim": "At least 1 uninterrupted clip",
        "coaching_sequence": "At least 1 complete sequence",
        "community_moment": "At least 1 natural moment",
    },
    "recording_standards": [
        "Clean the lens and confirm battery and free storage",
        "Use vertical 9:16 for most social clips and capture at least two horizontal clips",
        "Use 1080p for routine coverage; reserve higher resolution for deliberate hero footage",
        "Keep ordinary clips steady and approximately 8-15 seconds long",
        "Start two seconds before the action and continue two seconds afterward",
        "Keep complete movement in frame and avoid unnecessary zooming or panning",
    ],
}


def default_media_coverage_settings() -> dict[str, Any]:
    """Return an independent JSON-safe copy for a new vault."""

    return deepcopy(_DEFAULT_MEDIA_COVERAGE_SETTINGS)
