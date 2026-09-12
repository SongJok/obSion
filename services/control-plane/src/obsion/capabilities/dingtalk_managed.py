"""Explicit host-managed DWS SDK adapter for development document acquisition.

This adapter is installed by an operator-owned host worker, not by an Agent or
connector configuration. It exposes fixed reads, never arbitrary commands.
Application credentials still arrive through the Gateway CredentialBroker;
the independent DWS user session remains in the host's credential manager.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import select

from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.capabilities.dingtalk_docs import (
    DingTalkDocsDeniedError,
    DingTalkDocsResponseError,
    DingTalkDocsUnavailableError,
    normalize_document_id,
    normalize_workspace_id,
    resolve_dingtalk_docs_credentials,
)
from obsion.capabilities.dingtalk_wiki import DingTalkWikiClient
from obsion.common.errors import ValidationError
from obsion.common.time import utc_now
from obsion.db.models import Connector, ImInstallation, ImPrincipalBinding, User
from obsion.domain.enums import ConnectorStatus
from obsion.knowledge.dingtalk_jsonml import PARSER_VERSION, extract_full_document
from obsion.security.auth import load_principal_by_id

MANAGED_CONNECTOR_TYPE = "dingtalk-managed-development"
MANAGED_READ_OPERATION = "knowledge.dingtalk.read"
MANAGED_DISCOVER_OPERATION = "knowledge.dingtalk.discover"
MANAGED_OPERATIONS = frozenset({MANAGED_READ_OPERATION, MANAGED_DISCOVER_OPERATION})
_MAX_OUTPUT_BYTES = 10_000_000
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CONFIG_KEYS = frozenset(
    {"corp_id", "user_id", "operator_id", "app_key_env", "installation_id", "rate_limit_per_minute"}
)

READ_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation", "workspace_id", "node_id"],
    "properties": {
        "operation": {"const": MANAGED_READ_OPERATION},
        "workspace_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,128}$"},
        "node_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$"},
    },
}


DISCOVER_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation", "stage", "cursor"],
    "properties": {
        "operation": {"const": MANAGED_DISCOVER_OPERATION},
        "stage": {"enum": ["workspaces", "nodes"]},
        "cursor": {"type": ["string", "null"], "minLength": 1, "maxLength": 8192},
        "workspace_id": READ_INPUT_SCHEMA["properties"]["workspace_id"],
        "parent_id": READ_INPUT_SCHEMA["properties"]["node_id"],
    },
    "allOf": [
        {
            "if": {"properties": {"stage": {"const": "nodes"}}},
            "then": {"required": ["workspace_id", "parent_id"]},
            "else": {
                "not": {"anyOf": [{"required": ["workspace_id"]}, {"required": ["parent_id"]}]}
            },
        }
    ],
}


class DwsReadRunner(Protocol):
    async def read(self, *, profile: str, node_id: str) -> dict[str, Any]: ...


class HostDwsReadRunner:
    def __init__(self) -> None:
        # Resolved once from the operator's host. No payload/config/environment
        # variable can replace the executable, arguments or working directory.
        executable = shutil.which("dws")
        if executable is None:
            raise DingTalkDocsUnavailableError("The host-managed DWS reader is not installed")
        self._executable = str(Path(executable).resolve())

    async def _call(self, arguments: tuple[str, ...]) -> dict[str, Any]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR"}
        }
        try:
            process = await asyncio.create_subprocess_exec(
                self._executable,
                *arguments,
                "--format",
                "json",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                cwd="/",
            )
        except OSError as exc:
            raise DingTalkDocsUnavailableError("The host-managed reader could not start") from exc

        async def collect(stream: asyncio.StreamReader | None) -> bytes:
            assert stream is not None
            chunks: list[bytes] = []
            size = 0
            while data := await stream.read(65536):
                size += len(data)
                if size > _MAX_OUTPUT_BYTES:
                    raise DingTalkDocsResponseError("The managed read exceeded its output budget")
                chunks.append(data)
            return b"".join(chunks)

        tasks = [
            asyncio.create_task(collect(process.stdout)),
            asyncio.create_task(collect(process.stderr)),
        ]
        try:
            async with asyncio.timeout(60):
                stdout, _ = await asyncio.gather(*tasks)
                code = await process.wait()
        except BaseException:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
            await process.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if code:
            # stderr may contain upstream credentials or body fragments. Do not
            # put it in exceptions, logs, audit records or automatic retries.
            raise DingTalkDocsUnavailableError("The managed read failed; no content was accepted")
        try:
            result = json.loads(stdout)
        except (ValueError, RecursionError) as exc:
            raise DingTalkDocsResponseError("The managed reader returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise DingTalkDocsResponseError("The managed reader returned an invalid envelope")
        return result

    async def read(self, *, profile: str, node_id: str) -> dict[str, Any]:
        parts = profile.split(":")
        if len(parts) != 2 or any(_ID.fullmatch(value) is None for value in (*parts, node_id)):
            raise ValidationError(
                "dingtalk_docs_operation_invalid", "Invalid fixed reader identity"
            )

        async def selected_identity() -> dict[str, Any]:
            identities = await self._call(("profile", "list"))
            profiles = identities.get("profiles")
            if identities.get("success") is not True or not isinstance(profiles, list):
                raise DingTalkDocsDeniedError()
            matches = [
                item
                for item in profiles
                if isinstance(item, dict)
                and item.get("profile") == profile
                and item.get("corpId") == parts[0]
                and item.get("userId") == parts[1]
            ]
            if len(matches) != 1:
                raise DingTalkDocsDeniedError()
            return matches[0]

        identity = await selected_identity()
        if identity.get("status") == "expired":
            # profile list is a passive token snapshot. The documented auth
            # status command refreshes only this pinned identity, without login,
            # organization switching, or passing credentials to the agent.
            await self._call(("--profile", profile, "auth", "status"))
            identity = await selected_identity()
        if identity.get("status") != "active":
            raise DingTalkDocsDeniedError()
        return await self._call(
            (
                "--profile",
                profile,
                "doc",
                "+fetch",
                "--node",
                node_id,
                "--scope",
                "full",
                "--detail",
                "full",
            )
        )


class DingTalkManagedSdkExecutor:
    def __init__(
        self, runner: DwsReadRunner, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._runner = runner
        self._transport = transport

    async def _binding(
        self, connector: Connector, context: ConnectorContext, app_key: str
    ) -> ImPrincipalBinding:
        configuration = connector.configuration
        if context.session is None:
            raise DingTalkDocsDeniedError()
        current = await load_principal_by_id(
            context.session, context.principal.organization_id, context.principal.id
        )
        if not current.can("knowledge.write"):
            raise DingTalkDocsDeniedError()
        bindings = (
            await context.session.scalars(
                select(ImPrincipalBinding)
                .join(ImInstallation, ImInstallation.id == ImPrincipalBinding.installation_id)
                .join(User, User.id == ImPrincipalBinding.user_id)
                .where(
                    ImPrincipalBinding.organization_id == context.principal.organization_id,
                    ImInstallation.organization_id == context.principal.organization_id,
                    ImPrincipalBinding.user_id == context.principal.id,
                    User.organization_id == context.principal.organization_id,
                    User.active.is_(True),
                    ImPrincipalBinding.channel == "dingtalk",
                    ImInstallation.channel == "dingtalk",
                    ImPrincipalBinding.sender_id == configuration["user_id"],
                    ImPrincipalBinding.active.is_(True),
                    ImPrincipalBinding.revoked_at.is_(None),
                    ImInstallation.active.is_(True),
                    ImInstallation.revoked_at.is_(None),
                    ImInstallation.corp_id == configuration["corp_id"],
                    ImInstallation.app_key == app_key,
                )
                .execution_options(populate_existing=True)
            )
        ).all()
        matching = [
            binding
            for binding in bindings
            if str(binding.installation_id) == configuration["installation_id"]
        ]
        if len(matching) != 1:
            raise DingTalkDocsDeniedError()
        return matching[0]

    async def _discover(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        configuration = dict(connector.configuration)
        reference = connector.credential_ref
        app_key, app_secret = resolve_dingtalk_docs_credentials(connector, credential)
        binding = await self._binding(connector, context, app_key)
        client = DingTalkWikiClient(
            corp_id=configuration["corp_id"],
            operator_id=configuration["operator_id"],
            app_key=app_key,
            app_secret=app_secret,
            transport=self._transport,
        )
        try:
            page = (
                await client.workspace_page(payload["cursor"])
                if payload["stage"] == "workspaces"
                else await client.node_page(
                    workspace_id=payload["workspace_id"],
                    parent_id=payload["parent_id"],
                    cursor=payload["cursor"],
                )
            )
            await self._binding(connector, context, app_key)
            assert context.session is not None
            await context.session.refresh(connector)
            if (
                connector.status != ConnectorStatus.ACTIVE
                or connector.configuration != configuration
                or connector.credential_ref != reference
                or connector.environment != "development"
                or connector.connector_type != MANAGED_CONNECTOR_TYPE
                or connector.endpoint is not None
                or connector.allowed_egress
            ):
                raise DingTalkDocsDeniedError()
        finally:
            await client.aclose()
        observed_at = utc_now()
        return ConnectorResult(
            source=connector.name,
            resource=f"dingtalk-wiki://{configuration['corp_id']}",
            observed_at=observed_at,
            data={
                **page,
                **payload,
                "cursor": page["cursor"],
                "adapter": MANAGED_CONNECTOR_TYPE,
                "corp_id": configuration["corp_id"],
                "binding_id": str(binding.id),
                "reader_user_id": str(context.principal.id),
                "observed_at": observed_at.isoformat(),
            },
        )

    async def invoke(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        configuration = connector.configuration
        if (
            connector.connector_type != MANAGED_CONNECTOR_TYPE
            or connector.status != ConnectorStatus.ACTIVE
            or connector.environment != "development"
            or connector.organization_id != context.principal.organization_id
            or connector.endpoint is not None
            or bool(connector.allowed_egress)
            or not isinstance(configuration, dict)
            or set(configuration) - _CONFIG_KEYS
            or payload.get("operation") not in MANAGED_OPERATIONS
            or not context.principal.can("knowledge.write")
        ):
            raise ValidationError(
                "dingtalk_docs_operation_invalid", "Invalid managed read contract"
            )
        for key in ("corp_id", "user_id", "operator_id", "installation_id"):
            value = configuration.get(key)
            if not isinstance(value, str) or _ID.fullmatch(value) is None:
                raise ValidationError(
                    "dingtalk_docs_operation_invalid", "Reader identity is required"
                )
        from jsonschema import Draft202012Validator

        discovery = payload["operation"] == MANAGED_DISCOVER_OPERATION
        schema = DISCOVER_INPUT_SCHEMA if discovery else READ_INPUT_SCHEMA
        if not Draft202012Validator(schema).is_valid(payload):
            raise ValidationError("dingtalk_docs_operation_invalid", "Invalid managed input")
        if discovery:
            return await self._discover(connector, payload, credential, context)
        node, space = payload["node_id"], payload["workspace_id"]
        if not isinstance(node, str) or not isinstance(space, str):
            raise ValidationError(
                "dingtalk_docs_operation_invalid", "Document identity is required"
            )
        node, space = normalize_document_id(node), normalize_workspace_id(space)
        app_key, app_secret = resolve_dingtalk_docs_credentials(connector, credential)
        configuration_snapshot = json.dumps(configuration, sort_keys=True)
        credential_reference = connector.credential_ref
        binding = await self._binding(connector, context, app_key)
        client = DingTalkWikiClient(
            corp_id=configuration["corp_id"],
            operator_id=configuration["operator_id"],
            app_key=app_key,
            app_secret=app_secret,
            transport=self._transport,
        )
        try:
            await client.verify_document_reader(
                workspace_id=space,
                node_id=node,
                user_id=configuration["user_id"],
            )
            result = await self._runner.read(
                profile=f"{configuration['corp_id']}:{configuration['user_id']}",
                node_id=node,
            )
            body = extract_full_document(result, node)
            # A successful initial check is not a grant to publish after revocation.
            await client.verify_document_reader(
                workspace_id=space,
                node_id=node,
                user_id=configuration["user_id"],
            )
            await self._binding(connector, context, app_key)
            assert context.session is not None
            await context.session.refresh(connector)
            if (
                connector.status != ConnectorStatus.ACTIVE
                or connector.connector_type != MANAGED_CONNECTOR_TYPE
                or connector.environment != "development"
                or connector.endpoint is not None
                or bool(connector.allowed_egress)
                or connector.credential_ref != credential_reference
                or json.dumps(connector.configuration, sort_keys=True) != configuration_snapshot
            ):
                raise DingTalkDocsDeniedError()
        except DingTalkDocsDeniedError:
            raise DingTalkDocsDeniedError() from None
        except DingTalkDocsUnavailableError:
            raise DingTalkDocsUnavailableError() from None
        finally:
            await client.aclose()
        observed_at = utc_now()
        return ConnectorResult(
            source=connector.name,
            resource=f"dingtalk-wiki://{configuration['corp_id']}/{node}",
            observed_at=observed_at,
            data={
                "operation": MANAGED_READ_OPERATION,
                "adapter": MANAGED_CONNECTOR_TYPE,
                "corp_id": configuration["corp_id"],
                "workspace_id": space,
                "node_id": node,
                "binding_id": str(binding.id),
                "reader_user_id": str(context.principal.id),
                "title": body.title,
                "revision": body.revision,
                "text": body.text,
                "complete": body.complete,
                "gaps": list(body.gaps),
                "parser_version": PARSER_VERSION,
                "raw_checksum_sha256": body.raw_checksum,
                "observed_at": observed_at.isoformat(),
            },
        )
