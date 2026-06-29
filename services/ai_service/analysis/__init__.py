"""Stroke Lab — shared analysis infrastructure.

The legacy pose/metrics engine (``pipeline``, ``pose_pipeline``, ``summary``)
has been removed — the VLM coach (``services.ai_service.coach`` +
``services.ai_service.pipeline``) is the primary engine now. What remains in
this package is light, shared infra imported directly from its submodules:

    from services.ai_service.analysis.storage import download_storage_path, ...
    from services.ai_service.analysis.drills import resolve_drill
    from services.ai_service.analysis.version import STROKELAB_ENGINE_VERSION

All three stay import-light (no cv2 / mediapipe / torch) so the API service and
CI's openapi generation — neither of which installs the ML extras — import
cleanly. There is no lazy re-export surface anymore.
"""
