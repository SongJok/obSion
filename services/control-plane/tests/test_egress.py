import pytest

from obsion.common.errors import ValidationError
from obsion.security.egress import validate_model_endpoint


def test_model_endpoint_requires_exact_allowlisted_authority() -> None:
    validate_model_endpoint(
        "https://models.internal/v1",
        ["models.internal:443"],
        allow_insecure_loopback=False,
    )
    with pytest.raises(ValidationError, match="egress allowlist"):
        validate_model_endpoint(
            "https://metadata.internal/v1",
            ["models.internal:443"],
            allow_insecure_loopback=False,
        )


def test_insecure_model_endpoint_is_local_development_only() -> None:
    validate_model_endpoint(
        "http://127.0.0.1:11434/v1",
        [],
        allow_insecure_loopback=True,
    )
    with pytest.raises(ValidationError, match="TLS"):
        validate_model_endpoint(
            "http://127.0.0.1:11434/v1",
            ["127.0.0.1:11434"],
            allow_insecure_loopback=False,
        )
