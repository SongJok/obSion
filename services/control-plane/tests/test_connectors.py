from uuid import uuid4

import pytest

from obsion.capabilities.connectors import CredentialBroker, _endpoint_authority
from obsion.common.errors import ValidationError
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.models import Organization, SecretReference
from obsion.db.session import Database


def test_egress_authority_normalizes_scheme_host_and_port() -> None:
    assert _endpoint_authority("https://Observability.Internal/v1/query") == (
        "observability.internal",
        443,
    )
    assert _endpoint_authority("observability.internal:8443") == (
        "observability.internal",
        8443,
    )


def test_egress_authority_rejects_non_http_transports() -> None:
    with pytest.raises(ValueError):
        _endpoint_authority("file:///etc/passwd")


@pytest.mark.asyncio
async def test_secret_reference_resolution_is_organization_scoped(tmp_path, monkeypatch) -> None:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'credentials.db'}",
    )
    database = Database(settings)
    organization_id = uuid4()
    other_organization_id = uuid4()
    monkeypatch.setenv("OBSION_TEST_WAREHOUSE_TOKEN", "short-lived-test-token")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.sessions() as session, session.begin():
            session.add_all(
                [
                    Organization(
                        id=organization_id,
                        slug="credentials-test",
                        name="Credentials Test",
                        active=True,
                        settings={},
                    ),
                    Organization(
                        id=other_organization_id,
                        slug="credentials-other",
                        name="Credentials Other",
                        active=True,
                        settings={},
                    ),
                    SecretReference(
                        organization_id=organization_id,
                        name="warehouse-reader",
                        provider="env",
                        external_ref="env://OBSION_TEST_WAREHOUSE_TOKEN",
                        description="Test reference",
                    ),
                ]
            )
        async with database.sessions() as session:
            broker = CredentialBroker()
            assert (
                await broker.resolve(
                    "secret://warehouse-reader",
                    session=session,
                    organization_id=organization_id,
                )
                == "short-lived-test-token"
            )
            with pytest.raises(ValidationError, match="not available"):
                await broker.resolve(
                    "secret://warehouse-reader",
                    session=session,
                    organization_id=other_organization_id,
                )
    finally:
        await database.dispose()
