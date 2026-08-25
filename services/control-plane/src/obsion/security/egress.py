import ipaddress
from urllib.parse import urlparse

from obsion.common.errors import ValidationError


def validate_model_endpoint(
    value: str,
    allowed_authorities: list[str],
    *,
    allow_insecure_loopback: bool,
) -> None:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError(
            "model_endpoint_invalid", "Model endpoint must be an HTTP(S) base URL"
        )
    loopback = _is_loopback(parsed.hostname)
    if parsed.scheme != "https" and not (allow_insecure_loopback and loopback):
        raise ValidationError(
            "model_endpoint_tls_required",
            "Model endpoints must use TLS outside local development",
        )
    authority = _authority(value, parsed.scheme)
    allowed = {_authority(item, "https") for item in allowed_authorities}
    if authority not in allowed and not (allow_insecure_loopback and loopback):
        raise ValidationError(
            "model_endpoint_egress_denied",
            "Model endpoint is outside the configured egress allowlist",
        )


def _authority(value: str, default_scheme: str) -> tuple[str, int]:
    candidate = value if "://" in value else f"{default_scheme}://{value}"
    parsed = urlparse(candidate)
    if parsed.hostname is None or parsed.scheme not in {"http", "https"}:
        raise ValidationError(
            "model_endpoint_allowlist_invalid", "Model egress allowlist entry is invalid"
        )
    return parsed.hostname.casefold(), parsed.port or (443 if parsed.scheme == "https" else 80)


def _is_loopback(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
