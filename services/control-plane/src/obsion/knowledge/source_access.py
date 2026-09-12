"""Mandatory current-source guard, in addition to ordinary document ACLs."""

from sqlalchemy import String, and_, cast, exists, or_, select
from sqlalchemy.sql.elements import ColumnElement

from obsion.common.time import utc_now
from obsion.db.models import (
    Connector,
    Document,
    ImInstallation,
    ImPrincipalBinding,
    KnowledgeSyncItem,
    KnowledgeSyncSource,
    User,
)
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
)
from obsion.domain.enums import ConnectorStatus
from obsion.security.identity import Principal

MANAGED_KNOWLEDGE_SOURCE = "dingtalk-managed"


def external_access_clause(principal: Principal) -> ColumnElement[bool]:
    """Missing/expired leases and local admin status never grant external access.

    JSON snapshots use exact serialized equality across PostgreSQL and SQLite.
    Reordered configuration may conservatively stop access, but changed config
    cannot silently keep an old source authorization alive.
    """
    current = (
        select(KnowledgeSyncItem.id)
        .join(KnowledgeSyncSource, KnowledgeSyncSource.id == KnowledgeSyncItem.source_id)
        .join(
            ConnectorConfigurationVersion,
            ConnectorConfigurationVersion.id == KnowledgeSyncSource.connector_version_id,
        )
        .join(Connector, Connector.id == ConnectorConfigurationVersion.connector_id)
        .join(ImPrincipalBinding, ImPrincipalBinding.id == KnowledgeSyncSource.principal_binding_id)
        .join(ImInstallation, ImInstallation.id == ImPrincipalBinding.installation_id)
        .join(User, User.id == KnowledgeSyncSource.user_id)
        .where(
            KnowledgeSyncItem.document_id == Document.id,
            KnowledgeSyncItem.organization_id == principal.organization_id,
            KnowledgeSyncSource.organization_id == principal.organization_id,
            ConnectorConfigurationVersion.organization_id == principal.organization_id,
            Connector.organization_id == principal.organization_id,
            ImPrincipalBinding.organization_id == principal.organization_id,
            ImInstallation.organization_id == principal.organization_id,
            User.organization_id == principal.organization_id,
            KnowledgeSyncSource.user_id == principal.id,
            ImPrincipalBinding.user_id == KnowledgeSyncSource.user_id,
            User.active.is_(True),
            KnowledgeSyncItem.status == "READY",
            KnowledgeSyncItem.access_expires_at > utc_now(),
            KnowledgeSyncSource.active.is_(True),
            ImPrincipalBinding.active.is_(True),
            ImPrincipalBinding.revoked_at.is_(None),
            ImInstallation.active.is_(True),
            ImInstallation.revoked_at.is_(None),
            ImPrincipalBinding.channel == "dingtalk",
            ImInstallation.channel == "dingtalk",
            ImInstallation.corp_id == KnowledgeSyncSource.corp_id,
            ImInstallation.app_key == KnowledgeSyncSource.app_key,
            ImPrincipalBinding.sender_id
            == ConnectorConfigurationVersion.configuration["user_id"].as_string(),
            Connector.status == ConnectorStatus.ACTIVE,
            Connector.connector_type == ConnectorConfigurationVersion.connector_type,
            Connector.environment == ConnectorConfigurationVersion.environment,
            Connector.endpoint.is_not_distinct_from(ConnectorConfigurationVersion.endpoint),
            Connector.credential_ref.is_not_distinct_from(
                ConnectorConfigurationVersion.credential_ref
            ),
            cast(Connector.configuration, String)
            == cast(ConnectorConfigurationVersion.configuration, String),
            cast(Connector.declared_grants, String)
            == cast(ConnectorConfigurationVersion.declared_grants, String),
            cast(Connector.allowed_egress, String)
            == cast(ConnectorConfigurationVersion.allowed_egress, String),
            ~exists(
                select(ConnectorVersionRevocation.connector_version_id).where(
                    ConnectorVersionRevocation.organization_id == principal.organization_id,
                    ConnectorVersionRevocation.connector_version_id
                    == ConnectorConfigurationVersion.id,
                )
            ).correlate(ConnectorConfigurationVersion),
        )
        .correlate(Document)
    )
    return and_(
        Document.organization_id == principal.organization_id,
        or_(Document.source != MANAGED_KNOWLEDGE_SOURCE, exists(current)),
    )
