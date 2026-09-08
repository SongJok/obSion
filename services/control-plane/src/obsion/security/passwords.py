"""Password credential hashing and policy for operator-provisioned identities.

Obsion authenticates humans through an identity provider wherever one exists.
Password credentials exist for the bounded case where an organization operates
Obsion without OIDC: a local deployment, an evaluation tenant, or the initial
administrator who must exist before any provider is wired.

The plaintext password is never persisted, never logged, never placed in an
Event, an Audit record, or a Model Gateway prompt. Only a self-describing
memory-hard derivation is stored, and every comparison is constant time.
"""

import base64
import hashlib
import hmac
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Final

from obsion.common.errors import ValidationError

ALGORITHM: Final = "scrypt"

# 128 * r * n bytes of memory per derivation: 16 MiB at n=2^14, r=8. This is the
# OWASP-recommended floor for scrypt and stays inside the OpenSSL memory bound
# that `hashlib.scrypt` enforces, so no native tuning is required to deploy.
_DEFAULT_COST: Final = 2**14
_DEFAULT_BLOCK_SIZE: Final = 8
_DEFAULT_PARALLELISM: Final = 1
_DERIVED_KEY_BYTES: Final = 32
_SALT_BYTES: Final = 16
_MAX_MEMORY_BYTES: Final = 128 * 1024 * 1024
_MAX_PLAINTEXT_BYTES: Final = 1024
_ENCODED_FIELDS: Final = 4
_MAX_COST: Final = 2**20
_MAX_BLOCK_SIZE: Final = 64
_MAX_PARALLELISM: Final = 16

# Passwords that appear in every published breach corpus. This is a floor, not a
# substitute for an organization's own credential policy; deployments that need a
# real corpus check should front Obsion with an identity provider.
_UNIVERSALLY_BREACHED: Final = frozenset(
    {
        "000000",
        "111111",
        "123123",
        "123456",
        "12345678",
        "123456789",
        "1234567890",
        "abc123",
        "iloveyou",
        "letmein",
        "password",
        "password1",
        "qwerty",
        "qwerty123",
    }
)


@dataclass(frozen=True, slots=True)
class ScryptParameters:
    """The cost parameters a single derivation was produced with."""

    cost: int = _DEFAULT_COST
    block_size: int = _DEFAULT_BLOCK_SIZE
    parallelism: int = _DEFAULT_PARALLELISM

    def __post_init__(self) -> None:
        if self.cost < 2 or self.cost & (self.cost - 1):
            raise ValueError("scrypt cost must be a power of two greater than one")
        if self.block_size < 1 or self.parallelism < 1:
            raise ValueError("scrypt block size and parallelism must be positive")
        if self.cost > _MAX_COST:
            raise ValueError("scrypt cost exceeds the permitted upper bound")
        if self.block_size > _MAX_BLOCK_SIZE:
            raise ValueError("scrypt block size exceeds the permitted upper bound")
        if self.parallelism > _MAX_PARALLELISM:
            raise ValueError("scrypt parallelism exceeds the permitted upper bound")
        if 128 * self.block_size * self.cost > _MAX_MEMORY_BYTES:
            raise ValueError("scrypt parameters exceed the permitted memory bound")

    def encode(self) -> str:
        return f"n={self.cost},r={self.block_size},p={self.parallelism}"

    @classmethod
    def decode(cls, value: str) -> "ScryptParameters":
        fields: dict[str, int] = {}
        for part in value.split(","):
            key, separator, raw = part.partition("=")
            if not separator or not raw.isdigit():
                raise ValueError("scrypt parameters are malformed")
            fields[key] = int(raw)
        try:
            return cls(cost=fields["n"], block_size=fields["r"], parallelism=fields["p"])
        except KeyError as exc:
            raise ValueError("scrypt parameters are incomplete") from exc


@dataclass(frozen=True, slots=True)
class PasswordPolicy:
    """The minimum credential strength a deployment accepts.

    `allow_breached` exists only so a development operator can provision the
    throwaway local administrator documented in the developer guide. Staging and
    production reject it at the call site, not here.
    """

    min_length: int = 12
    max_length: int = 256
    allow_breached: bool = False

    def enforce(self, plaintext: str) -> str:
        """Return the normalized password or raise `password_policy_violation`."""

        normalized = normalize(plaintext)
        if len(normalized) < self.min_length:
            raise ValidationError(
                "password_policy_violation",
                "The password is shorter than the configured minimum length",
                min_length=self.min_length,
            )
        if len(normalized) > self.max_length:
            raise ValidationError(
                "password_policy_violation",
                "The password is longer than the configured maximum length",
                max_length=self.max_length,
            )
        if not self.allow_breached and normalized.casefold() in _UNIVERSALLY_BREACHED:
            raise ValidationError(
                "password_policy_violation",
                "The password appears in published breach corpora",
            )
        return normalized


def normalize(plaintext: str) -> str:
    """Apply NFKC so the same typed password verifies across input methods.

    Without normalization a password entered through a full-width or composed
    input method derives a different key than the one enrolled, which reads to
    the user as an unexplainable authentication failure.
    """

    normalized = unicodedata.normalize("NFKC", plaintext)
    if not normalized:
        raise ValidationError("password_policy_violation", "The password is empty")
    if len(normalized.encode("utf-8")) > _MAX_PLAINTEXT_BYTES:
        raise ValidationError(
            "password_policy_violation",
            "The password exceeds the permitted input size",
        )
    return normalized


def hash_password(plaintext: str, *, parameters: ScryptParameters | None = None) -> str:
    """Derive a self-describing `scrypt$params$salt$key` credential."""

    resolved = parameters or ScryptParameters()
    normalized = normalize(plaintext)
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        normalized.encode("utf-8"),
        salt=salt,
        n=resolved.cost,
        r=resolved.block_size,
        p=resolved.parallelism,
        dklen=_DERIVED_KEY_BYTES,
        maxmem=_MAX_MEMORY_BYTES,
    )
    return "$".join((ALGORITHM, resolved.encode(), _encode(salt), _encode(derived)))


def verify_password(plaintext: str, encoded: str) -> bool:
    """Constant-time verification that never raises on a malformed credential.

    A stored credential that cannot be parsed is treated as non-matching rather
    than as an error, so a corrupted row fails closed instead of turning into a
    server fault that distinguishes it from a wrong password.
    """

    try:
        parameters, salt, expected = _decode(encoded)
        candidate = hashlib.scrypt(
            normalize(plaintext).encode("utf-8"),
            salt=salt,
            n=parameters.cost,
            r=parameters.block_size,
            p=parameters.parallelism,
            dklen=len(expected),
            maxmem=_MAX_MEMORY_BYTES,
        )
    except (ValidationError, ValueError):
        return False
    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str, *, parameters: ScryptParameters | None = None) -> bool:
    """Report whether a stored credential predates the current cost parameters."""

    resolved = parameters or ScryptParameters()
    try:
        stored, _, _ = _decode(encoded)
    except ValueError:
        return True
    return stored != resolved


def verification_sentinel(*, parameters: ScryptParameters | None = None) -> str:
    """Return a non-secret derivation envelope for equivalent rejection work.

    Unknown identities and identities without an enrolled credential must still
    pay the configured KDF cost. Otherwise response time becomes an account
    enumeration oracle even when the HTTP status and body are identical. The
    fixed bytes are not a credential and intentionally cannot authenticate a
    real user.
    """

    resolved = parameters or ScryptParameters()
    return "$".join(
        (
            ALGORITHM,
            resolved.encode(),
            _encode(bytes(_SALT_BYTES)),
            _encode(bytes(_DERIVED_KEY_BYTES)),
        )
    )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode_segment(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _decode(encoded: str) -> tuple[ScryptParameters, bytes, bytes]:
    fields = encoded.split("$")
    if len(fields) != _ENCODED_FIELDS or fields[0] != ALGORITHM:
        raise ValueError("The stored credential is not a supported derivation")
    parameters = ScryptParameters.decode(fields[1])
    salt = _decode_segment(fields[2])
    expected = _decode_segment(fields[3])
    if not salt or not expected:
        raise ValueError("The stored credential is incomplete")
    return parameters, salt, expected
