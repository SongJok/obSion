"""Versioned, read-only Codeup contracts; no provider credentials or automatic bindings."""

from typing import Any

CODEUP_ORIGIN = "https://openapi-rdc.aliyuncs.com"
CODEUP_PROTOCOL = "codeup.read.v1"
CODEUP_CATALOG_PROTOCOL = "codeup.catalog.v1"
CODEUP_DISCOVERY_OPERATION = "codeup.repositories.discover"
CODEUP_OPERATIONS = frozenset(
    {"codeup.repository.get", "codeup.commit.get", "codeup.commits.list", "codeup.file.read"}
)
CODEUP_ALL_OPERATIONS = CODEUP_OPERATIONS | {CODEUP_DISCOVERY_OPERATION}
MAX_FILE_BYTES = 262_144
MAX_RESPONSE_BYTES = 2_097_152
MAX_PAGE_SIZE = 50


def input_schema(operation: str) -> dict[str, Any]:
    if operation == CODEUP_DISCOVERY_OPERATION:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["operation"],
            "properties": {
                "operation": {"const": CODEUP_DISCOVERY_OPERATION},
                "search": {"type": "string", "minLength": 1, "maxLength": 100},
                "page": {"type": "integer", "minimum": 1, "maximum": 100},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
        }
    properties: dict[str, Any] = {
        "operation": {"const": operation},
        "repository": {"type": "string", "minLength": 1, "maxLength": 240},
    }
    required = ["operation", "repository"]
    if operation in {"codeup.commit.get", "codeup.file.read"}:
        properties["commit_id"] = {
            "type": "string",
            "pattern": "^[a-f0-9]{40}$",
            "minLength": 40,
            "maxLength": 40,
        }
        required.append("commit_id")
    if operation == "codeup.file.read":
        properties["path"] = {"type": "string", "minLength": 1, "maxLength": 1024}
        required.append("path")
    if operation == "codeup.commits.list":
        properties.update(
            {
                "ref": {"type": "string", "minLength": 1, "maxLength": 200},
                "page": {"type": "integer", "minimum": 1, "maximum": 100},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE_SIZE},
            }
        )
        required.append("ref")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


def output_schema(operation: str) -> dict[str, Any]:
    if operation == CODEUP_DISCOVERY_OPERATION:
        discovery_fields: dict[str, Any] = {
            "id": {"type": "string", "pattern": "^[1-9][0-9]{0,19}$"},
            "name": {"type": "string", "minLength": 1, "maxLength": 240},
            "path": {"type": "string", "minLength": 1, "maxLength": 500},
            "visibility": {"enum": ["private", "internal"]},
            "archived": {"type": "boolean"},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "operation",
                "connector_id",
                "items",
                "count",
                "next_page",
                "complete",
            ],
            "properties": {
                "operation": {"const": CODEUP_DISCOVERY_OPERATION},
                "connector_id": {"type": "string", "format": "uuid"},
                "items": {
                    "type": "array",
                    "maxItems": 20,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(discovery_fields),
                        "properties": discovery_fields,
                    },
                },
                "count": {"type": "integer", "minimum": 0, "maximum": 20},
                "next_page": {"type": ["integer", "null"], "minimum": 2, "maximum": 100},
                "complete": {"type": "boolean"},
            },
        }
    sha = {"type": "string", "pattern": "^[a-f0-9]{40}$", "minLength": 40, "maxLength": 40}
    if operation == "codeup.repository.get":
        fields: dict[str, Any] = {
            "id": {"type": "string", "pattern": "^[1-9][0-9]{0,19}$"},
            "name": {"type": "string", "maxLength": 4000},
            "default_branch": {"type": "string", "maxLength": 200},
            "visibility": {"enum": ["private", "internal"]},
        }
    elif operation == "codeup.file.read":
        fields = {
            "path": {"type": "string", "minLength": 1, "maxLength": 1024},
            "commit_id": sha,
            "blob_id": sha,
            "content": {"type": "string", "maxLength": MAX_FILE_BYTES},
            "source_size_bytes": {"type": "integer", "minimum": 0, "maximum": MAX_FILE_BYTES},
            "redacted": {"type": "boolean"},
            "content_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$", "maxLength": 64},
        }
    else:
        fields = {
            "commit_id": sha,
            "parent_ids": {"type": "array", "maxItems": 64, "items": sha},
            "committed_at": {"type": "string", "format": "date-time", "maxLength": 80},
            "title": {"type": "string", "maxLength": 4000},
            "message": {"type": "string", "maxLength": 4000},
        }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "operation",
            "repository",
            "repository_id",
            "items",
            "count",
            "next_page",
            "complete",
        ],
        "properties": {
            "operation": {"const": operation},
            "repository": {"type": "string"},
            "repository_id": {"type": "string", "format": "uuid"},
            "items": {
                "type": "array",
                "minItems": 0 if operation == "codeup.commits.list" else 1,
                "maxItems": MAX_PAGE_SIZE if operation == "codeup.commits.list" else 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": list(fields),
                    "properties": fields,
                },
            },
            "count": {"type": "integer", "minimum": 0, "maximum": MAX_PAGE_SIZE},
            "next_page": {"type": ["integer", "null"], "minimum": 2, "maximum": 100},
            "complete": {"type": "boolean"},
        },
    }
