"""Organization-bound discovery through the current DingTalk Wiki v2 API.

This read adapter shares the existing authenticated connector transport. Wiki
v2 node IDs must never be sent to the legacy document-content endpoints.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from typing import Any

from obsion.capabilities.dingtalk_docs import (
    DingTalkDocsClient,
    DingTalkDocsDeniedError,
    DingTalkDocsResponseError,
    DingTalkDocument,
    DingTalkWorkspace,
    DingTalkWorkspaceNode,
    normalize_document_id,
    normalize_workspace_id,
)
from obsion.common.errors import ValidationError
from obsion.knowledge.connector_contract import KnowledgeConnectorBudget, SyncBudgetTracker

DINGTALK_WIKI_PROTOCOL = "dingtalk.wiki.v2"
WIKI_WORKSPACES_PATH = "/v2.0/wiki/workspaces"
WIKI_NODES_PATH = "/v2.0/wiki/nodes"
_ROLES = frozenset({"OWNER", "MANAGER", "EDITOR", "DOWNLOADER", "READER"})


def _required_text(item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DingTalkDocsResponseError("DingTalk Wiki response omitted a required identifier")
    return value.strip()


def _page(payload: Mapping[str, Any], key: str) -> tuple[list[Mapping[str, Any]], str | None]:
    items = payload.get(key)
    if (
        ("success" in payload and payload["success"] is not True)
        or not isinstance(items, list)
        or any(not isinstance(item, Mapping) for item in items)
    ):
        raise DingTalkDocsResponseError("DingTalk Wiki response omitted a valid collection")
    token = payload.get("nextToken")
    if token is not None and not isinstance(token, str):
        raise DingTalkDocsResponseError("DingTalk Wiki response contained an invalid cursor")
    if isinstance(token, str) and token and not token.strip():
        raise DingTalkDocsResponseError("DingTalk Wiki response contained a blank cursor")
    # A cursor is opaque: do not trim or rewrite it before the next request.
    cursor = token or None
    if "hasMore" in payload and (
        not isinstance(payload["hasMore"], bool) or payload["hasMore"] != bool(cursor)
    ):
        raise DingTalkDocsResponseError("DingTalk Wiki pagination was inconsistent")
    return items, cursor


class DingTalkWikiClient(DingTalkDocsClient):
    def __init__(
        self,
        *,
        corp_id: str,
        operator_id: str,
        **kwargs: Any,
    ) -> None:
        if not corp_id.strip() or not operator_id.strip():
            raise ValidationError(
                "dingtalk_docs_operation_invalid",
                "Wiki v2 requires an explicit organization and operator unionId",
            )
        self._corp_id = corp_id.strip()
        self._operator_id = operator_id.strip()
        self._roots: dict[str, str] = {}
        super().__init__(**kwargs)

    async def _pages(
        self, path: str, key: str, params: dict[str, str], tracker: SyncBudgetTracker
    ) -> list[Mapping[str, Any]]:
        result: list[Mapping[str, Any]] = []
        seen_cursors: set[str] = set()
        query = {**params, "operatorId": self._operator_id, "withPermissionRole": "true"}
        while True:
            tracker.consume_page()
            payload = await self._request_json("GET", path, authorized=True, params=query)
            items, cursor = _page(payload, key)
            result.extend(items)
            if cursor is None:
                return result
            if cursor in seen_cursors:
                raise DingTalkDocsResponseError("DingTalk Wiki repeated a pagination cursor")
            seen_cursors.add(cursor)
            query["nextToken"] = cursor

    async def workspace_page(self, cursor: str | None = None) -> dict[str, Any]:
        """One bounded organization page; callers persist the opaque continuation."""
        query = {"operatorId": self._operator_id, "withPermissionRole": "true", "maxResults": "30"}
        if cursor is not None:
            query["nextToken"] = cursor
        result = await self._request_json(
            "GET", WIKI_WORKSPACES_PATH, authorized=True, params=query
        )
        items, next_cursor = _page(result, "workspaces")
        if len(items) > 30 or (next_cursor is not None and len(next_cursor) > 8192):
            raise DingTalkDocsResponseError("Workspace page exceeded its bounds")
        spaces: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            corp, kind = _required_text(item, "corpId"), _required_text(item, "type")
            if corp != self._corp_id or kind == "PERSONAL":
                continue
            if kind != "TEAM" or item.get("permissionRole") not in _ROLES:
                raise DingTalkDocsDeniedError()
            space = normalize_workspace_id(_required_text(item, "workspaceId"))
            if space in seen:
                raise DingTalkDocsResponseError("DingTalk Wiki returned duplicate workspaces")
            seen.add(space)
            spaces.append(
                {
                    "workspace_id": space,
                    "root_node_id": normalize_document_id(_required_text(item, "rootNodeId")),
                    "title": _required_text(item, "name"),
                }
            )
        return {"items": spaces, "cursor": next_cursor}

    async def node_page(
        self, *, workspace_id: str, parent_id: str, cursor: str | None = None
    ) -> dict[str, Any]:
        """One child page after fresh authoritative organization/parent checks.

        Workspace authorization retains the existing explicit page budget. A
        budget failure is retriable/incomplete, never an empty successful tree.
        """
        space = normalize_workspace_id(workspace_id)
        parent = normalize_document_id(parent_id)
        await self.list_workspaces()
        if space not in self._roots:
            raise DingTalkDocsDeniedError()
        if parent != self._roots[space]:
            response = await self._request_json(
                "GET",
                f"{WIKI_NODES_PATH}/{parent}",
                authorized=True,
                params={"operatorId": self._operator_id, "withPermissionRole": "true"},
            )
            item = response.get("node")
            if (
                ("success" in response and response["success"] is not True)
                or not isinstance(item, Mapping)
                or item.get("nodeId") != parent
                or item.get("workspaceId") != space
                or item.get("permissionRole") not in _ROLES
            ):
                raise DingTalkDocsDeniedError()
        query = {
            "operatorId": self._operator_id,
            "withPermissionRole": "true",
            "parentNodeId": parent,
            "maxResults": "50",
        }
        if cursor is not None:
            query["nextToken"] = cursor
        result = await self._request_json("GET", WIKI_NODES_PATH, authorized=True, params=query)
        items, next_cursor = _page(result, "nodes")
        if len(items) > 50 or (next_cursor is not None and len(next_cursor) > 8192):
            raise DingTalkDocsResponseError("Node page exceeded its bounds")
        nodes: list[dict[str, Any]] = []
        seen = {parent, self._roots[space]}
        for item in items:
            if item.get("workspaceId") != space or item.get("permissionRole") not in _ROLES:
                raise DingTalkDocsDeniedError()
            node = normalize_document_id(_required_text(item, "nodeId"))
            if node in seen:
                raise DingTalkDocsResponseError("DingTalk Wiki returned a duplicate or cyclic node")
            seen.add(node)
            kind, children = item.get("type"), item.get("hasChildren")
            if kind not in {"FILE", "FOLDER"} or not isinstance(children, bool):
                raise DingTalkDocsResponseError("DingTalk Wiki returned an invalid node")
            extension = item.get("extension")
            nodes.append(
                {
                    "workspace_id": space,
                    "node_id": node,
                    "title": _required_text(item, "name"),
                    "kind": extension.casefold()
                    if kind == "FILE" and isinstance(extension, str) and extension
                    else str(kind).casefold(),
                    "has_children": children,
                }
            )
        return {"items": nodes, "cursor": next_cursor}

    async def list_workspaces(
        self, *, tracker: SyncBudgetTracker | None = None
    ) -> list[DingTalkWorkspace]:
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        self._roots = {}
        items = await self._pages(WIKI_WORKSPACES_PATH, "workspaces", {"maxResults": "30"}, budget)
        spaces: list[DingTalkWorkspace] = []
        roots: dict[str, str] = {}
        for item in items:
            corp_id = _required_text(item, "corpId")
            space_type = _required_text(item, "type")
            if corp_id != self._corp_id or space_type == "PERSONAL":
                continue
            if space_type != "TEAM":
                raise DingTalkDocsResponseError("DingTalk Wiki returned an unknown space type")
            if item.get("permissionRole") not in _ROLES:
                raise DingTalkDocsDeniedError()
            workspace_id = normalize_workspace_id(_required_text(item, "workspaceId"))
            root = normalize_document_id(_required_text(item, "rootNodeId"))
            if workspace_id in roots:
                raise DingTalkDocsResponseError("DingTalk Wiki returned a duplicate workspace")
            roots[workspace_id] = root
            spaces.append(
                DingTalkWorkspace(
                    workspace_id=workspace_id,
                    name=_required_text(item, "name"),
                    description=str(item.get("description") or ""),
                )
            )
        self._roots = roots
        return spaces

    async def list_workspace_nodes(
        self, workspace_id: str, *, tracker: SyncBudgetTracker | None = None
    ) -> list[DingTalkWorkspaceNode]:
        space = normalize_workspace_id(workspace_id)
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        # Refresh the authoritative organization scope on every traversal. A
        # caller-supplied workspace ID is never enough to authorize discovery.
        await self.list_workspaces(tracker=budget)
        root = self._roots.get(space)
        if root is None:
            raise DingTalkDocsDeniedError()
        pending = deque([(root, 1)])
        visited = {root}
        nodes: list[DingTalkWorkspaceNode] = []
        while pending:
            parent, depth = pending.popleft()
            budget.enter_depth(depth)
            items = await self._pages(
                WIKI_NODES_PATH,
                "nodes",
                {"maxResults": "50", "parentNodeId": parent},
                budget,
            )
            for item in items:
                if _required_text(item, "workspaceId") != space:
                    raise DingTalkDocsDeniedError()
                if item.get("permissionRole") not in _ROLES:
                    raise DingTalkDocsDeniedError()
                node = normalize_document_id(_required_text(item, "nodeId"))
                if node in visited:
                    raise DingTalkDocsResponseError(
                        "DingTalk Wiki returned a cycle or duplicate node"
                    )
                visited.add(node)
                budget.consume_node()
                node_type = _required_text(item, "type")
                if node_type not in {"FILE", "FOLDER"} or not isinstance(
                    item.get("hasChildren"), bool
                ):
                    raise DingTalkDocsResponseError("DingTalk Wiki returned an invalid node type")
                extension = item.get("extension")
                # Preserve type distinctions: an AI table is not a text document.
                item_type = (
                    str(extension).casefold()
                    if node_type == "FILE" and isinstance(extension, str) and extension
                    else node_type.casefold()
                )
                nodes.append(
                    DingTalkWorkspaceNode(
                        workspace_id=space,
                        node_id=node,
                        document_id=node if node_type == "FILE" else "",
                        node_type=item_type,
                        title=_required_text(item, "name"),
                    )
                )
                if item["hasChildren"]:
                    pending.append((node, depth + 1))
        return nodes

    async def verify_document_reader(
        self, *, workspace_id: str, node_id: str, user_id: str
    ) -> None:
        """Require a same-organization, explicit USER read grant before body access.

        Inherited organization/group memberships are deliberately not mapped to
        local users. READER grants preview, not the READ privilege.
        """
        space = normalize_workspace_id(workspace_id)
        node = normalize_document_id(node_id)
        budget = SyncBudgetTracker(KnowledgeConnectorBudget())
        await self.list_workspaces(tracker=budget)
        if space not in self._roots:
            raise DingTalkDocsDeniedError()
        result = await self._request_json(
            "GET",
            f"{WIKI_NODES_PATH}/{node}",
            authorized=True,
            params={"operatorId": self._operator_id, "withPermissionRole": "true"},
        )
        item = result.get("node")
        read_roles = {"OWNER", "MANAGER", "EDITOR", "DOWNLOADER"}
        if (
            ("success" in result and result["success"] is not True)
            or not isinstance(item, Mapping)
            or item.get("nodeId") != node
            or item.get("workspaceId") != space
            or item.get("type") != "FILE"
            or item.get("extension") != "adoc"
            or item.get("permissionRole") not in read_roles
        ):
            raise DingTalkDocsDeniedError()
        seen: set[str] = set()
        cursor: str | None = None
        permitted = False
        while True:
            budget.consume_page()
            option: dict[str, Any] = {"maxResults": 30}
            if cursor is not None:
                option["nextToken"] = cursor
            response = await self._request_json(
                "POST",
                f"/v2.0/storage/spaces/dentries/{node}/permissions/query",
                authorized=True,
                params={"unionId": self._operator_id},
                json_body={"option": option},
            )
            permissions, cursor = _page(response, "permissions")
            for permission in permissions:
                member, role = permission.get("member"), permission.get("role")
                if (
                    permission.get("dentryUuid") != node
                    or not isinstance(member, Mapping)
                    or not isinstance(role, Mapping)
                ):
                    raise DingTalkDocsResponseError("The document permission entry was invalid")
                if (
                    member.get("type") == "USER"
                    and member.get("corpId") == self._corp_id
                    and member.get("id") == user_id
                    and role.get("id") in read_roles
                    # Time-limited grants need a separately verified duration contract.
                    and permission.get("duration") is None
                ):
                    permitted = True
            if cursor is None:
                break
            if cursor in seen:
                raise DingTalkDocsResponseError("The document permission cursor repeated")
            seen.add(cursor)
        if not permitted:
            raise DingTalkDocsDeniedError()

    async def fetch_document(
        self, *, document_id: str, inherit_acl: bool = False
    ) -> DingTalkDocument:
        raise ValidationError(
            "dingtalk_docs_operation_invalid",
            "Wiki v2 content and ACL synchronization are not configured; "
            "legacy IDs cannot be reused",
        )
