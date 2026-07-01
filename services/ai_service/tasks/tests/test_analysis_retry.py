from services.ai_service.tasks.analyze import (
    _coach_has_content,
    _main_coach_retryable_error,
    _main_coach_system_error,
    _retry_payload,
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


def test_gate_service_unavailable_error_is_retryable_before_refusal():
    err = _main_coach_retryable_error(
        _payload(
            {
                "component": "gate",
                "error": "ServiceUnavailableError: GeminiException code 503",
                "findings": [],
            }
        )
    )
    assert err and "ServiceUnavailableError" in err


def test_segment_rate_limit_error_is_retryable():
    err = _main_coach_retryable_error(
        _payload(
            {
                "component": "phase_segment",
                "error": "RateLimitError: resource exhausted 429",
                "findings": [],
            }
        )
    )
    assert err and "RateLimitError" in err


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


def test_gate_auth_error_is_system_error():
    err = _main_coach_system_error(
        _payload(
            {
                "component": "gate",
                "error": "AuthenticationError: code 401",
                "findings": [],
            }
        )
    )
    assert err and "AuthenticationError" in err


def test_retry_payload_exposes_retry_metadata_without_existing_result():
    payload = _retry_payload({}, attempt=1, delay_seconds=120, queued_at=_dt())

    retry = payload["retry"]
    result = payload["result"]
    assert payload["partial"] is True
    assert retry["status"] == "retrying"
    assert retry["attempt"] == 1
    assert result["meta"]["ai_coach_retry"] == retry
    assert result["results"] == []


def _dt():
    from datetime import UTC, datetime

    return datetime(2026, 7, 1, tzinfo=UTC)
