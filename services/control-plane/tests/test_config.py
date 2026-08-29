import pytest
from pydantic import ValidationError as PydanticValidationError

from obsion.config import AuthMode, Environment, Settings


def test_blank_optional_environment_values_are_treated_as_unset() -> None:
    settings = Settings(
        _env_file=None,
        oidc_issuer="",
        oidc_audience=" ",
        oidc_jwks_url="",
        otel_exporter_otlp_endpoint="",
        otel_exporter_headers="",
    )

    assert settings.oidc_issuer is None
    assert settings.oidc_audience is None
    assert settings.oidc_jwks_url is None
    assert settings.otel_exporter_otlp_endpoint is None


def test_production_rejects_development_authentication() -> None:
    with pytest.raises(PydanticValidationError, match="development authentication"):
        Settings(_env_file=None, environment=Environment.PRODUCTION, auth_mode=AuthMode.DEVELOPMENT)


def test_production_browser_sessions_require_explicit_origins() -> None:
    with pytest.raises(PydanticValidationError, match="explicit allowed origins"):
        Settings(
            _env_file=None,
            environment=Environment.PRODUCTION,
            auth_mode=AuthMode.OIDC,
            allowed_origins=["*"],
            oidc_issuer="https://identity.example",
            oidc_audience="obsion",
            oidc_jwks_url="https://identity.example/.well-known/jwks.json",
        )


def test_memory_default_ttl_cannot_exceed_retention_boundary() -> None:
    with pytest.raises(PydanticValidationError, match="memory_default_ttl_days"):
        Settings(_env_file=None, memory_default_ttl_days=31, memory_max_ttl_days=30)


def test_conversation_message_budget_cannot_exceed_total_budget() -> None:
    with pytest.raises(PydanticValidationError, match="conversation_context_max_chars"):
        Settings(
            _env_file=None,
            conversation_context_max_chars=4_000,
            conversation_context_max_chars_per_message=8_000,
        )
