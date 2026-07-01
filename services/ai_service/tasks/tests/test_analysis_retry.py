from services.ai_service.tasks.analyze import (
    _coach_has_content,
    _main_coach_retryable_error,
    _main_coach_system_error,
)


def _payload(*components):
    return {"result": {"results": list(components)}}


def test_collate_only_output_is_not_coaching_content():
    assert (
        _coach_has_content(
            _payload(
                {"component": "gate", "findings": [{"severity": "info"}]},
                {"component": "collate", "findings": [{"severity": "info"}]},
            )
        )
        is False
    )


def test_chunk_coach_finding_counts_as_coaching_content():
    assert (
        _coach_has_content(
            _payload(
                {
                    "component": "chunk_coach",
                    "findings": [{"severity": "fix", "available": True}],
                }
            )
        )
        is True
    )


def test_chunk_coach_internal_error_is_retryable():
    err = _main_coach_retryable_error(
        _payload(
            {
                "component": "chunk_coach",
                "error": "InternalServerError: GeminiException code 500",
                "findings": [],
            }
        )
    )
    assert err and "InternalServerError" in err


def test_non_transient_chunk_coach_error_is_not_retryable():
    assert (
        _main_coach_retryable_error(
            _payload(
                {
                    "component": "chunk_coach",
                    "error": "ValueError: bad response shape",
                    "findings": [],
                }
            )
        )
        is None
    )


def test_chunk_coach_auth_error_is_system_error():
    err = _main_coach_system_error(
        _payload(
            {
                "component": "chunk_coach",
                "error": (
                    "AuthenticationError: GeminiException code 401 "
                    "ACCESS_TOKEN_TYPE_UNSUPPORTED"
                ),
                "findings": [],
            }
        )
    )
    assert err and "AuthenticationError" in err


def test_non_chunk_coach_auth_error_is_not_system_error():
    assert (
        _main_coach_system_error(
            _payload(
                {
                    "component": "gate",
                    "error": "AuthenticationError: code 401",
                    "findings": [],
                }
            )
        )
        is None
    )
