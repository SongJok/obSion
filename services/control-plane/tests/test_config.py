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
