from obsion.security.masking import apply_obligations
from obsion.security.redaction import redact, redact_text


def test_recursive_redaction_never_retains_secrets() -> None:
    payload = {
        "Authorization": "Bearer live-secret-token",
        "nested": {
            "api_key": "sk-live-value",
            "dsn": "postgresql://admin:hunter2@db.internal/warehouse",
        },
    }
    result = redact(payload)
    assert result["Authorization"] == "[REDACTED]"
    assert result["nested"]["api_key"] == "[REDACTED]"
    assert "hunter2" not in result["nested"]["dsn"]
    assert "live-secret-token" not in redact_text("Bearer live-secret-token")


def test_text_redaction_covers_assignments_and_private_key_blocks() -> None:
    text = (
        "password='do not persist' api_key: sk-live-value "
        "-----BEGIN PRIVATE KEY-----secret material-----END PRIVATE KEY-----"
    )
    redacted = redact_text(text)
    assert "do not persist" not in redacted
    assert "sk-live-value" not in redacted
    assert "secret material" not in redacted
    assert "[REDACTED]" in redacted
    assert "[REDACTED PRIVATE KEY]" in redacted


def test_masking_is_non_mutating_and_enforces_row_limits() -> None:
    payload = {
        "customer": {"email": "person@example.com", "id": "42"},
        "rows": [{"value": index} for index in range(5)],
    }
    result = apply_obligations(
        payload,
        (
            {"type": "mask_fields", "fields": ["customer.email"]},
            {"type": "hash_fields", "fields": ["customer.id"]},
            {"type": "limit_result_rows", "value": 2},
        ),
    )
    assert result["customer"]["email"] == "***"
    assert result["customer"]["id"] != "42"
    assert len(result["rows"]) == 2
    assert payload["customer"]["email"] == "person@example.com"
    assert len(payload["rows"]) == 5
