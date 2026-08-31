"""供应商知识源浏览 Capability 的稳定、供应商无关合同。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

KNOWLEDGE_SOURCE_CONTAINERS = "knowledge.source.containers"
KNOWLEDGE_SOURCE_ITEMS = "knowledge.source.items"
VENDOR_KNOWLEDGE_BROWSE_OPERATIONS = frozenset(
    {KNOWLEDGE_SOURCE_CONTAINERS, KNOWLEDGE_SOURCE_ITEMS}
)
VENDOR_KNOWLEDGE_SOURCES = ("feishu", "dingtalk", "wecom", "confluence")


@dataclass(frozen=True, slots=True)
class VendorKnowledgeContainer:
    container_id: str
    name: str
    description: str = ""
    key: str | None = None


@dataclass(frozen=True, slots=True)
class VendorKnowledgeItem:
    container_id: str
    item_id: str
    document_id: str
    item_type: str
    title: str
    status: str | None = None


def containers_result(
    source: str,
    containers: Iterable[VendorKnowledgeContainer],
) -> dict[str, Any]:
    return {
        "operation": KNOWLEDGE_SOURCE_CONTAINERS,
        "source": source,
        "containers": [asdict(item) for item in containers],
    }


def items_result(
    source: str,
    items: Iterable[VendorKnowledgeItem],
) -> dict[str, Any]:
    return {
        "operation": KNOWLEDGE_SOURCE_ITEMS,
        "source": source,
        "items": [asdict(item) for item in items],
    }


CONTAINERS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation"],
    "properties": {
        "operation": {"type": "string", "const": KNOWLEDGE_SOURCE_CONTAINERS},
        "container_id": {"type": "string", "minLength": 1, "maxLength": 128},
    },
}

ITEMS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation", "container_id"],
    "properties": {
        "operation": {"type": "string", "const": KNOWLEDGE_SOURCE_ITEMS},
        "container_id": {"type": "string", "minLength": 1, "maxLength": 128},
    },
}

_CONTAINER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["container_id", "name", "description", "key"],
    "properties": {
        "container_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "name": {"type": "string", "minLength": 1, "maxLength": 1000},
        "description": {"type": "string", "maxLength": 4000},
        "key": {"type": ["string", "null"], "maxLength": 128},
    },
}

_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "container_id",
        "item_id",
        "document_id",
        "item_type",
        "title",
        "status",
    ],
    "properties": {
        "container_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "item_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "document_id": {"type": "string", "maxLength": 128},
        "item_type": {"type": "string", "minLength": 1, "maxLength": 128},
        "title": {"type": "string", "minLength": 1, "maxLength": 1000},
        "status": {"type": ["string", "null"], "maxLength": 128},
    },
}

CONTAINERS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation", "source", "containers"],
    "properties": {
        "operation": {"type": "string", "const": KNOWLEDGE_SOURCE_CONTAINERS},
        "source": {"type": "string", "enum": list(VENDOR_KNOWLEDGE_SOURCES)},
        "containers": {"type": "array", "maxItems": 1000, "items": _CONTAINER_SCHEMA},
    },
    "allOf": [
        {
            "if": {"properties": {"source": {"const": "wecom"}}, "required": ["source"]},
            "then": {"properties": {"containers": {"minItems": 1, "maxItems": 1}}},
        }
    ],
}

ITEMS_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation", "source", "items"],
    "properties": {
        "operation": {"type": "string", "const": KNOWLEDGE_SOURCE_ITEMS},
        "source": {"type": "string", "enum": list(VENDOR_KNOWLEDGE_SOURCES)},
        "items": {"type": "array", "maxItems": 5000, "items": _ITEM_SCHEMA},
    },
}
