"""Pipeline components.

Phase 1: ``gate`` (3-tier view/usability) and ``holistic_coach`` (the validated
gpt-4o coach), both wrapping proven logic in ``services.ai_service.coach``.
Phase 2 will add Stage-0/1 components (track, recovery-segment) and per-instance
analyzers — they slot in behind the same Component contract.
"""
