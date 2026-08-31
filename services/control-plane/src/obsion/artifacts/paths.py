from __future__ import annotations

import re

from obsion.common.errors import ValidationError

_MAX_PATH = 512
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def normalize_workspace_path(path: str | None) -> str | None:
    """Return a governed workspace file path, or None when the caller omitted one."""

    if path is None:
        return None
    raw = path.strip()
    if raw == "":
        return None
    if len(raw) > _MAX_PATH or not raw.startswith("/") or raw.endswith("/"):
        raise ValidationError(
            "artifact_path_invalid",
            "Workspace file paths must be an absolute file path of at most 512 characters",
        )
    parts = raw.split("/")[1:]
    unsafe = any(part in {"", ".", ".."} or _SEGMENT.fullmatch(part) is None for part in parts)
    if not parts or unsafe:
        raise ValidationError(
            "artifact_path_invalid",
            "Workspace file paths cannot contain empty, relative, or unsafe segments",
        )
    return "/" + "/".join(parts)
