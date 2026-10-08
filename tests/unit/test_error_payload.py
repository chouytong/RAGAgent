from ragagent.errors import error_payload


def test_errors_only_expose_safe_static_codes_and_manual_retry_guidance() -> None:
    for field in ["sk-" + "SYNTHETIC" * 4, "Bearer " + "SYNTHETIC" * 8, "raw private text"]:
        payload = error_payload(field, field)
        assert payload["error_code"] == "internal_error" and payload["request_id"] is None
        assert field not in str(payload)
    assert error_payload("local_auth_required")["retryable"] is False
    assert error_payload("infrastructure_unavailable")["retryable"] is True
    assert error_payload("provider_key_missing")["details"] is None
