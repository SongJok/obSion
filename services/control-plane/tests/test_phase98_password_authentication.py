"""Local password credentials: derivation, policy, lockout, and login."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from obsion.bootstrap import bootstrap_development_identity
from obsion.cli import build_parser
from obsion.common.errors import ObsionError, ValidationError
from obsion.config import Environment, Settings
from obsion.db.session import Database
from obsion.domain.enums import SystemRole
from obsion.main import create_app
from obsion.persistence import user_credentials
from obsion.persistence.user_credentials import CredentialOutcome, UserCredentialStore
from obsion.release.notes import validate_release_notes
from obsion.security.passwords import (
    ALGORITHM,
    PasswordPolicy,
    ScryptParameters,
    hash_password,
    needs_rehash,
    normalize,
    verify_password,
)
from obsion.security.provisioning import provision_password_identity

_ADMIN_EMAIL = "phase98-admin@example.com"
_ADMIN_PASSWORD = "phase98-correct-horse"  # noqa: S105
_CHEAP = ScryptParameters(cost=2**10, block_size=1, parallelism=1)
_REPOSITORY_ROOT = Path(__file__).parents[3]


@pytest.fixture
def cheap_password_settings(app_settings: Settings) -> Settings:
    """Rebuild the settings with derivation cost low enough for a test suite."""

    settings = app_settings.model_copy(
        update={
            "password_scrypt_cost": _CHEAP.cost,
            "password_scrypt_block_size": _CHEAP.block_size,
            "password_max_failed_attempts": 3,
        }
    )
    _run(settings, bootstrap_development_identity)
    return settings


@pytest.fixture
def password_client(cheap_password_settings: Settings) -> Iterator[TestClient]:
    _provision(
        cheap_password_settings,
        email=_ADMIN_EMAIL,
        password=_ADMIN_PASSWORD,
        role=SystemRole.ADMIN,
    )
    with TestClient(create_app(cheap_password_settings)) as test_client:
        yield test_client


def _run(settings: Settings, operation: Any, **kwargs: Any) -> Any:
    async def run() -> Any:
        database = Database(settings)
        try:
            async with database.sessions() as session:
                result = await operation(session, settings, **kwargs)
                await session.commit()
                return result
        finally:
            await database.dispose()

    return asyncio.run(run())


def _provision(settings: Settings, **kwargs: Any) -> Any:
    return _run(settings, provision_password_identity, **kwargs)


def test_derivation_is_salted_self_describing_and_constant_time_verified() -> None:
    first = hash_password("a-sufficiently-long-password", parameters=_CHEAP)
    second = hash_password("a-sufficiently-long-password", parameters=_CHEAP)

    assert first != second, "each enrolment must use a fresh salt"
    assert first.split("$")[0] == ALGORITHM
    assert first.split("$")[1] == "n=1024,r=1,p=1"
    assert verify_password("a-sufficiently-long-password", first)
    assert verify_password("a-sufficiently-long-password", second)
    assert not verify_password("a-sufficiently-long-passworD", first)


def test_derivation_never_embeds_the_plaintext() -> None:
    encoded = hash_password("plaintext-must-not-appear", parameters=_CHEAP)

    assert "plaintext-must-not-appear" not in encoded


@pytest.mark.parametrize(
    "encoded",
    [
        "",
        "scrypt",
        "scrypt$n=1024,r=1,p=1$only-three-fields",
        "argon2$n=1024,r=1,p=1$c2FsdA$a2V5",
        "scrypt$n=notanumber,r=1,p=1$c2FsdA$a2V5",
        "scrypt$r=1,p=1$c2FsdA$a2V5",
        "scrypt$n=1024,r=1,p=1$$a2V5",
    ],
)
def test_a_malformed_stored_credential_fails_closed(encoded: str) -> None:
    assert verify_password("any-candidate-password", encoded) is False


def test_normalization_makes_equivalent_input_methods_verify() -> None:
    composed = normalize("ｐａｓｓｗｏｒｄ-ｌｏｎｇ")
    encoded = hash_password("ｐａｓｓｗｏｒｄ-ｌｏｎｇ", parameters=_CHEAP)

    assert composed == "password-long"
    assert verify_password("password-long", encoded)


def test_policy_rejects_short_long_breached_and_empty_passwords() -> None:
    policy = PasswordPolicy(min_length=12, max_length=32)

    for candidate in ("short", "x" * 33, "", "123456"):
        with pytest.raises(ValidationError) as raised:
            policy.enforce(candidate)
        assert raised.value.code == "password_policy_violation"

    assert policy.enforce("a-compliant-password") == "a-compliant-password"


def test_policy_can_admit_a_breached_password_only_when_asked() -> None:
    assert PasswordPolicy(min_length=6, allow_breached=True).enforce("123456") == "123456"


def test_rehash_is_requested_only_when_cost_parameters_change() -> None:
    encoded = hash_password("a-sufficiently-long-password", parameters=_CHEAP)

    assert not needs_rehash(encoded, parameters=_CHEAP)
    assert needs_rehash(encoded, parameters=ScryptParameters(cost=2**11, block_size=1))
    assert needs_rehash("not-a-credential", parameters=_CHEAP)


def test_scrypt_parameters_reject_values_outside_the_memory_bound() -> None:
    with pytest.raises(ValueError, match="power of two"):
        ScryptParameters(cost=1000)
    with pytest.raises(ValueError, match="memory bound"):
        ScryptParameters(cost=2**20, block_size=64)
    with pytest.raises(ValueError, match="parallelism"):
        ScryptParameters(cost=2**10, block_size=1, parallelism=17)


def test_an_unknown_identity_still_performs_equivalent_derivation(
    cheap_password_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verified: list[str] = []

    def record_verification(plaintext: str, encoded: str) -> bool:
        del plaintext
        verified.append(encoded)
        return False

    monkeypatch.setattr(user_credentials, "verify_password", record_verification)

    async def reject_unknown(session: Any, settings: Settings) -> Any:
        store = UserCredentialStore(
            parameters=_CHEAP,
            max_failed_attempts=settings.password_max_failed_attempts,
            lockout_seconds=settings.password_lockout_seconds,
        )
        return await store.verify(
            session,
            email="phase98-absent@example.com",
            plaintext="a-candidate-password",
        )

    result = _run(cheap_password_settings, reject_unknown)

    assert result.outcome is CredentialOutcome.REJECTED
    assert len(verified) == 1
    assert verified[0].startswith("scrypt$n=1024,r=1,p=1$")


def test_provisioning_grants_the_role_and_is_idempotent(
    cheap_password_settings: Settings,
) -> None:
    first = _provision(
        cheap_password_settings,
        email="Phase98-Repeat@example.com",
        password="an-initial-password",
        role=SystemRole.ADMIN,
        display_name="Repeat Admin",
    )
    second = _provision(
        cheap_password_settings,
        email="phase98-repeat@example.com",
        password="a-rotated-password",
        role=SystemRole.ADMIN,
    )

    assert first.created is True
    assert second.created is False
    assert first.user_id == second.user_id
    assert second.roles == ("admin",)
    assert second.display_name == "Repeat Admin", "an existing display name is preserved"


def test_provisioning_refuses_a_weak_password_outside_development(
    cheap_password_settings: Settings,
) -> None:
    staging = cheap_password_settings.model_copy(update={"environment": Environment.STAGING})

    with pytest.raises(ObsionError) as raised:
        _provision(
            staging,
            email="phase98-weak@example.com",
            password="123456",
            role=SystemRole.VIEWER,
            allow_weak_password=True,
        )

    assert raised.value.code == "password_policy_violation"


def test_password_login_issues_a_browser_session(password_client: TestClient) -> None:
    response = password_client.post(
        "/api/v1/auth/password-session",
        json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["roles"] == ["admin"]
    assert response.cookies.get("obsion_session")
    assert response.headers["cache-control"] == "no-store"

    established = password_client.get("/api/v1/auth/session")
    assert established.status_code == 200, established.text
    assert established.json()["principal_id"] == body["principal_id"]


def test_password_login_accepts_a_differently_cased_email(password_client: TestClient) -> None:
    response = password_client.post(
        "/api/v1/auth/password-session",
        json={"email": _ADMIN_EMAIL.upper(), "password": _ADMIN_PASSWORD},
    )

    assert response.status_code == 201, response.text


def test_a_wrong_password_and_an_unknown_email_are_indistinguishable(
    password_client: TestClient,
) -> None:
    wrong = password_client.post(
        "/api/v1/auth/password-session",
        json={"email": _ADMIN_EMAIL, "password": "not-the-enrolled-password"},
    )
    unknown = password_client.post(
        "/api/v1/auth/password-session",
        json={"email": "phase98-absent@example.com", "password": _ADMIN_PASSWORD},
    )

    assert wrong.status_code == unknown.status_code == 403
    assert wrong.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert wrong.json()["message"] == unknown.json()["message"]
    assert not wrong.cookies.get("obsion_session")


def test_repeated_failures_lock_the_credential_and_survive_the_correct_password(
    password_client: TestClient,
) -> None:
    for _ in range(3):
        rejected = password_client.post(
            "/api/v1/auth/password-session",
            json={"email": _ADMIN_EMAIL, "password": "not-the-enrolled-password"},
        )
    assert rejected.json()["code"] == "credential_locked"

    locked = password_client.post(
        "/api/v1/auth/password-session",
        json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
    )
    assert locked.status_code == 429, locked.text
    assert locked.json()["code"] == "credential_locked"


def test_a_successful_login_clears_earlier_failed_attempts(
    password_client: TestClient,
) -> None:
    password_client.post(
        "/api/v1/auth/password-session",
        json={"email": _ADMIN_EMAIL, "password": "not-the-enrolled-password"},
    )
    password_client.post(
        "/api/v1/auth/password-session",
        json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
    )
    password_client.cookies.clear()

    for _ in range(2):
        response = password_client.post(
            "/api/v1/auth/password-session",
            json={"email": _ADMIN_EMAIL, "password": "not-the-enrolled-password"},
        )
        assert response.json()["code"] == "invalid_credentials", "the counter did not reset"


def test_password_login_can_be_disabled_for_a_deployment(
    cheap_password_settings: Settings,
) -> None:
    _provision(
        cheap_password_settings,
        email=_ADMIN_EMAIL,
        password=_ADMIN_PASSWORD,
        role=SystemRole.ADMIN,
    )
    disabled = cheap_password_settings.model_copy(update={"password_auth_enabled": False})

    with TestClient(create_app(disabled)) as disabled_client:
        response = disabled_client.post(
            "/api/v1/auth/password-session",
            json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "password_auth_disabled"


def test_password_login_rejects_a_cross_origin_request(password_client: TestClient) -> None:
    response = password_client.post(
        "/api/v1/auth/password-session",
        json={"email": _ADMIN_EMAIL, "password": _ADMIN_PASSWORD},
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "request_origin_denied"


def test_provisioning_cli_never_accepts_a_password_argument() -> None:
    parser = build_parser()
    parsed = parser.parse_args(
        ["provision-user", "--email", "phase98-cli@example.com", "--role", "admin"]
    )
    assert not hasattr(parsed, "password")

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "provision-user",
                "--email",
                "phase98-cli@example.com",
                "--password",
                "must-not-enter-process-arguments",
            ]
        )


def test_environment_example_covers_settings_and_matches_local_key_set() -> None:
    def keys(path: Path) -> set[str]:
        return {
            line.partition("=")[0]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#") and "=" in line
        }

    example_keys = keys(_REPOSITORY_ROOT / ".env.example")
    setting_keys = {f"OBSION_{name.upper()}" for name in Settings.model_fields}
    local_ai_keys = {"OBSION_AI_API_KEY", "OBSION_AI_BASE_URL", "OBSION_AI_MODEL"}

    assert setting_keys <= example_keys
    assert local_ai_keys <= example_keys
    local_environment = _REPOSITORY_ROOT / ".env"
    if local_environment.exists():
        assert keys(local_environment) == example_keys


def test_local_compose_loads_optional_environment_only_into_the_api() -> None:
    compose = yaml.safe_load((_REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert compose["services"]["api"]["env_file"] == [{"path": ".env", "required": False}]
    assert "env_file" not in compose["services"]["migrate"]


def test_phase98_release_contract_status_and_migration_ci_are_pinned() -> None:
    release = validate_release_notes(
        _REPOSITORY_ROOT / "docs/release/0.98.0-dev.yaml",
        _REPOSITORY_ROOT,
    )
    status = yaml.safe_load(
        (_REPOSITORY_ROOT / "docs/project-status.yaml").read_text(encoding="utf-8")
    )
    workflow = (_REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert release["version"] == "0.98.0-dev"
    assert release["phase"] == 98
    assert release["database_migration"] == "alembic"
    assert status["version"] == "0.98.0-dev"
    assert status["current_phase"] == "phase-98"
    assert status["completed_phases"][-1] == "phase-98"
    assert status["next_phase"]["id"] == "phase-99"
    assert "OBSION_RUN_PHASE98_MIGRATION_TEST" in workflow
    assert "test_postgres_phase98_user_credential_migration.py" in workflow
    containers = workflow.split("  containers:", 1)[1].split("\n  java-sdk:", 1)[0]
    assert "migration-round-trips" in containers.split("needs:", 1)[1].split("\n", 1)[0]
    for job_start, job_end in (
        ("  audit-log-migration:", "  phase2-identity-migration:"),
        ("  phase2-identity-migration:", "  migration-round-trips:"),
        ("  migration-round-trips:", "  containers:"),
    ):
        job = workflow.split(job_start, 1)[1].split(job_end, 1)[0]
        upgrade = "alembic -c services/control-plane/alembic.ini upgrade head"
        check = "alembic -c services/control-plane/alembic.ini check"
        assert upgrade in job
        assert job.index(upgrade) < job.index(check)
