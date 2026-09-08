"""有界项目副本传输契约；来源 pin 是待核验引用，不是授权凭证。

仅消费上游已授权的文件字节，不读取宿主仓库、不解析 Git ref、不联网。
首个切片仅支持 UTF-8 普通文件，拒绝凭据路径和已知 Secret 形状。
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import struct
import unicodedata
import zipfile
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from obsion.release.hardening import scan_secret_text

MAX_PROJECT_FILES = 500
MAX_PROJECT_ENTRIES = 2000
MAX_FILE_BYTES = 262144
MAX_PROJECT_BYTES = 8388608
MAX_ARCHIVE_BYTES = MAX_PROJECT_BYTES + 1048576
MAX_MANIFEST_BYTES = 524288
_BLOCKED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".ssh",
        ".aws",
        ".azure",
        ".kube",
        ".gnupg",
        ".config",
        ".dws",
        "dws",
        ".dingtalk",
        ".dingtalk-workspace",
        ".obsion",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".gitconfig",
        ".git-credentials",
        "credentials",
        "credentials.json",
        "kubeconfig",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
    }
)


class ProjectRejected(ValueError):
    """仅包含固定拒绝原因，不回显文件内容、路径或凭据。"""


def validate_project_path(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ProjectRejected("project_path_invalid")
    if unicodedata.normalize("NFC", value) != value or any(
        unicodedata.category(char).startswith("C") or char in '\\:"' for char in value
    ):
        raise ProjectRejected("project_path_invalid")
    parts = value.split("/")
    if len(parts) > 32 or any(
        not part
        or part in {".", ".."}
        or part.endswith((" ", "."))
        or len(part.encode("utf-8")) > 255
        for part in parts
    ):
        raise ProjectRejected("project_path_invalid")
    for part in parts:
        lowered = part.casefold()
        if (
            lowered in _BLOCKED_PARTS
            or lowered.startswith(".env")
            or lowered.endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))
            or re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", lowered)
        ):
            raise ProjectRejected("project_path_reserved")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _check_archive_directory(data: bytes) -> None:
    # ZipFile 会先物化整个中央目录；先限制真实条目数，不能只信 EOCD 声明数量。
    if len(data) < 22 or data[-22:-18] != b"PK\x05\x06":
        raise ProjectRejected("project_archive_invalid")
    _, disk, start_disk, count_disk, count, size, offset, comment = struct.unpack(
        "<4s4H2LH", data[-22:]
    )
    if (
        disk
        or start_disk
        or comment
        or count_disk != count
        or not 1 <= count <= MAX_PROJECT_FILES + 1
        or offset + size != len(data) - 22
    ):
        raise ProjectRejected("project_archive_invalid")
    position, seen = offset, 0
    while position < offset + size:
        if position + 46 > len(data) - 22 or data[position : position + 4] != b"PK\x01\x02":
            raise ProjectRejected("project_archive_invalid")
        name_size, extra_size, comment_size = struct.unpack_from("<3H", data, position + 28)
        if extra_size or comment_size or not 1 <= name_size <= 4096:
            raise ProjectRejected("project_archive_invalid")
        position += 46 + name_size
        seen += 1
        if seen > MAX_PROJECT_FILES + 1 or position > offset + size:
            raise ProjectRejected("project_archive_invalid")
    if seen != count:
        raise ProjectRejected("project_archive_invalid")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectRejected("project_manifest_invalid")
        result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ProjectRevision:
    organization_id: UUID
    workspace_id: UUID
    repository_id: UUID
    connector_version_id: UUID
    commit_id: str
    tree_id: str

    def __post_init__(self) -> None:
        for value in (
            self.organization_id,
            self.workspace_id,
            self.repository_id,
            self.connector_version_id,
        ):
            if not isinstance(value, UUID) or value.int == 0:
                raise ProjectRejected("project_revision_invalid")
        if any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value)
            for value in (self.commit_id, self.tree_id)
        ) or len(self.commit_id) != len(self.tree_id):
            raise ProjectRejected("project_revision_invalid")

    def to_wire(self) -> dict[str, str]:
        return {
            "organization_id": str(self.organization_id),
            "workspace_id": str(self.workspace_id),
            "repository_id": str(self.repository_id),
            "connector_version_id": str(self.connector_version_id),
            "commit_id": self.commit_id,
            "tree_id": self.tree_id,
        }

    @classmethod
    def from_wire(cls, value: Any) -> ProjectRevision:
        keys = {
            "organization_id",
            "workspace_id",
            "repository_id",
            "connector_version_id",
            "commit_id",
            "tree_id",
        }
        if (
            not isinstance(value, dict)
            or set(value) != keys
            or any(not isinstance(item, str) for item in value.values())
        ):
            raise ProjectRejected("project_revision_invalid")
        try:
            revision = cls(
                UUID(value["organization_id"]),
                UUID(value["workspace_id"]),
                UUID(value["repository_id"]),
                UUID(value["connector_version_id"]),
                value["commit_id"],
                value["tree_id"],
            )
        except (TypeError, ValueError):
            raise ProjectRejected("project_revision_invalid") from None
        if revision.to_wire() != value:
            raise ProjectRejected("project_revision_invalid")
        return revision


@dataclass(frozen=True, slots=True)
class ProjectFile:
    path: str
    content: bytes = field(repr=False)
    executable: bool = False

    def __post_init__(self) -> None:
        validate_project_path(self.path)
        if type(self.content) is not bytes or len(self.content) > MAX_FILE_BYTES:
            raise ProjectRejected("project_file_size_invalid")
        if type(self.executable) is not bool:
            raise ProjectRejected("project_file_mode_invalid")
        try:
            text = self.content.decode("utf-8")
        except UnicodeError:
            raise ProjectRejected("project_file_not_utf8") from None
        if "\x00" in text:
            raise ProjectRejected("project_file_not_text")
        # 不跳过 tests/fixture；文件名允许不代表内容中不会含凭据。
        if scan_secret_text(text, path=self.path):
            raise ProjectRejected("project_secret_detected")

    def metadata(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": _sha256(self.content),
            "size": len(self.content),
            "executable": self.executable,
        }


@dataclass(frozen=True, slots=True)
class ProjectSnapshot:
    revision: ProjectRevision
    files: tuple[ProjectFile, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.revision, ProjectRevision):
            raise ProjectRejected("project_revision_invalid")
        if not isinstance(self.files, tuple) or len(self.files) > MAX_PROJECT_FILES:
            raise ProjectRejected("project_file_count_exceeded")
        if any(not isinstance(item, ProjectFile) for item in self.files):
            raise ProjectRejected("project_file_invalid")
        if sum(len(item.content) for item in self.files) > MAX_PROJECT_BYTES:
            raise ProjectRejected("project_size_exceeded")
        paths: set[str] = set()
        directories: dict[str, str] = {}
        for item in sorted(self.files, key=lambda item: item.path):
            folded = item.path.casefold()
            if folded in paths or folded in directories:
                raise ProjectRejected("project_path_collision")
            parts = item.path.split("/")
            for count in range(1, len(parts)):
                parent = "/".join(parts[:count])
                key = parent.casefold()
                if key in paths or (key in directories and directories[key] != parent):
                    raise ProjectRejected("project_path_collision")
                directories[key] = parent
            paths.add(folded)
        if len(paths) + len(directories) > MAX_PROJECT_ENTRIES:
            raise ProjectRejected("project_entry_count_exceeded")
        if len(_canonical(self.manifest())) > MAX_MANIFEST_BYTES:
            raise ProjectRejected("project_manifest_size_exceeded")

    def manifest(self) -> dict[str, Any]:
        return {
            "version": 1,
            "revision": self.revision.to_wire(),
            "files": [item.metadata() for item in sorted(self.files, key=lambda item: item.path)],
        }

    @property
    def fingerprint(self) -> str:
        return _sha256(_canonical(self.manifest()))

    def to_archive(self) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            entries = [("manifest.json", _canonical(self.manifest()), False)]
            entries.extend(
                ("files/" + item.path, item.content, item.executable)
                for item in sorted(self.files, key=lambda item: item.path)
            )
            for name, content, executable in entries:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
                archive.writestr(info, content)
        result = buffer.getvalue()
        if len(result) > MAX_ARCHIVE_BYTES:
            raise ProjectRejected("project_archive_size_exceeded")
        return result

    @classmethod
    def from_archive(cls, data: bytes, *, expected_sha256: str) -> ProjectSnapshot:
        if type(data) is not bytes or len(data) > MAX_ARCHIVE_BYTES:
            raise ProjectRejected("project_archive_size_exceeded")
        if not isinstance(expected_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_sha256
        ):
            raise ProjectRejected("project_archive_checksum_invalid")
        if _sha256(data) != expected_sha256:
            raise ProjectRejected("project_archive_checksum_mismatch")
        _check_archive_directory(data)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                snapshot = cls._read_archive(archive)
            # 只接受本版本规范容器，拒绝未登记前缀、尾随字节和隐蔽扩展载荷。
            if snapshot.to_archive() != data:
                raise ProjectRejected("project_archive_not_canonical")
            return snapshot
        except (zipfile.BadZipFile, UnicodeError, KeyError, RuntimeError, EOFError, OverflowError):
            raise ProjectRejected("project_archive_invalid") from None

    @classmethod
    def _read_archive(cls, archive: zipfile.ZipFile) -> ProjectSnapshot:
        infos = archive.infolist()
        if archive.comment or not 1 <= len(infos) <= MAX_PROJECT_FILES + 1:
            raise ProjectRejected("project_archive_invalid")
        names: set[str] = set()
        for info in infos:
            if (
                info.filename in names
                or info.orig_filename != info.filename
                or info.compress_type != zipfile.ZIP_STORED
                or info.compress_size != info.file_size
                or info.file_size
                > (MAX_MANIFEST_BYTES if info.filename == "manifest.json" else MAX_FILE_BYTES)
                or info.flag_bits & ~0x800
                or info.extra
                or info.comment
                or info.create_system != 3
                or info.external_attr >> 16 not in {stat.S_IFREG | 0o644, stat.S_IFREG | 0o755}
            ):
                raise ProjectRejected("project_archive_invalid")
            names.add(info.filename)
        try:
            manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            raise ProjectRejected("project_manifest_invalid") from None
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {"version", "revision", "files"}
            or type(manifest["version"]) is not int
            or manifest["version"] != 1
            or not isinstance(manifest["files"], list)
            or len(manifest["files"]) > MAX_PROJECT_FILES
        ):
            raise ProjectRejected("project_manifest_invalid")
        revision = ProjectRevision.from_wire(manifest["revision"])
        files = []
        total_bytes = 0
        for metadata in manifest["files"]:
            if not isinstance(metadata, dict) or set(metadata) != {
                "path",
                "sha256",
                "size",
                "executable",
            }:
                raise ProjectRejected("project_manifest_invalid")
            validate_project_path(metadata["path"])
            if type(metadata["size"]) is not int or not 0 <= metadata["size"] <= MAX_FILE_BYTES:
                raise ProjectRejected("project_manifest_invalid")
            total_bytes += metadata["size"]
            if total_bytes > MAX_PROJECT_BYTES:
                raise ProjectRejected("project_size_exceeded")
            info = archive.getinfo("files/" + metadata["path"])
            if info.file_size != metadata["size"]:
                raise ProjectRejected("project_manifest_mismatch")
            item = ProjectFile(metadata["path"], archive.read(info), metadata["executable"])
            if (
                item.metadata() != metadata
                or bool((info.external_attr >> 16) & 0o111) != item.executable
            ):
                raise ProjectRejected("project_manifest_mismatch")
            files.append(item)
        snapshot = cls(revision, tuple(files))
        if names != {"manifest.json", *("files/" + item.path for item in files)}:
            raise ProjectRejected("project_manifest_mismatch")
        if snapshot.manifest() != manifest:
            raise ProjectRejected("project_manifest_mismatch")
        return snapshot
