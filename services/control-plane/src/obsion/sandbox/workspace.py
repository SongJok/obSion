"""仅供可信入口操作独占、静止的项目目录；不作为宿主机沙箱或公开文件接口。

目录 fd 与 O_NOFOLLOW 约束逐层访问，拒绝链接和特殊文件。仍要求上游确认
全部项目进程停止后导出，文件系统检查不能替代 gVisor/cgroups 隔离。
"""

from __future__ import annotations

import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from obsion.sandbox.project import (
    MAX_FILE_BYTES,
    MAX_PROJECT_BYTES,
    MAX_PROJECT_ENTRIES,
    MAX_PROJECT_FILES,
    ProjectFile,
    ProjectRejected,
    ProjectRevision,
    ProjectSnapshot,
    validate_project_path,
)

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


def _regular(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ProjectRejected("project_file_type_rejected")
    if stat.S_IMODE(info.st_mode) not in {0o600, 0o644, 0o700, 0o755}:
        raise ProjectRejected("project_file_mode_rejected")
    if info.st_size > MAX_FILE_BYTES:
        raise ProjectRejected("project_file_size_invalid")


def _read(directory: int, name: str, path: str) -> ProjectFile:
    # 先 lstat，避免 FIFO 等特殊对象产生阻塞；open/fstat 再检查以关闭替换窗口。
    _regular(os.stat(name, dir_fd=directory, follow_symlinks=False))
    fd = os.open(name, _FILE_FLAGS, dir_fd=directory)
    try:
        before = os.fstat(fd)
        _regular(before)
        with os.fdopen(os.dup(fd), "rb") as stream:
            content = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(fd)
        _regular(after)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(content) != before.st_size:
            raise ProjectRejected("project_file_changed_during_read")
        return ProjectFile(path, content, bool(before.st_mode & 0o111))
    finally:
        os.close(fd)


class ProjectWorkspace:
    """root 由可信入口指定且独占；方法不授予项目/来源/任务权限。"""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or len(root.parts) < 2 or ".." in root.parts:
            raise ProjectRejected("project_root_invalid")
        self._fd = -1
        directory = os.open("/", _DIR_FLAGS)
        try:
            for part in root.parts[1:]:
                child = os.open(part, _DIR_FLAGS, dir_fd=directory)
                os.close(directory)
                directory = child
            self._fd = directory
        except OSError:
            os.close(directory)
            raise ProjectRejected("project_root_invalid") from None

    def __enter__(self) -> ProjectWorkspace:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._fd != -1:
            os.close(self._fd)
            self._fd = -1

    def _require_open(self) -> None:
        if self._fd == -1:
            raise ProjectRejected("project_workspace_closed")

    @contextmanager
    def _parent(self, path: str, *, create: bool = False) -> Iterator[tuple[int, str]]:
        self._require_open()
        validate_project_path(path)
        parts = path.split("/")
        directory = os.dup(self._fd)
        try:
            for part in parts[:-1]:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, mode=0o700, dir_fd=directory)
                child = os.open(part, _DIR_FLAGS, dir_fd=directory)
                os.close(directory)
                directory = child
            yield directory, parts[-1]
        except OSError:
            raise ProjectRejected("project_filesystem_rejected") from None
        finally:
            os.close(directory)

    def read_file(self, path: str) -> ProjectFile:
        with self._parent(path) as (directory, name):
            return _read(directory, name, path)

    def write_file(self, item: ProjectFile) -> None:
        if not isinstance(item, ProjectFile):
            raise ProjectRejected("project_file_invalid")
        with self._parent(item.path, create=True) as (directory, name):
            with suppress(FileNotFoundError):
                _regular(os.stat(name, dir_fd=directory, follow_symlinks=False))
            temporary = ".obsion-write-" + secrets.token_hex(16)
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory,
            )
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(item.content)
                    stream.flush()
                    os.fchmod(stream.fileno(), 0o755 if item.executable else 0o644)
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=directory)

    def remove_file(self, path: str) -> None:
        with self._parent(path) as (directory, name):
            _regular(os.stat(name, dir_fd=directory, follow_symlinks=False))
            os.unlink(name, dir_fd=directory)

    def materialize(self, snapshot: ProjectSnapshot) -> None:
        self._require_open()
        if not isinstance(snapshot, ProjectSnapshot):
            raise ProjectRejected("project_snapshot_invalid")
        # 不覆盖调用者已有内容，也不因错误递归删除可能不属于本次操作的目录。
        with os.scandir(self._fd) as entries:
            if next(entries, None) is not None:
                raise ProjectRejected("project_workspace_not_empty")
        for item in snapshot.files:
            self.write_file(item)

    def capture(self, revision: ProjectRevision) -> ProjectSnapshot:
        self._require_open()
        files: list[ProjectFile] = []
        total_bytes = 0
        entries_seen = 0

        def visit(directory: int, prefix: str) -> None:
            nonlocal total_bytes, entries_seen
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > MAX_PROJECT_ENTRIES:
                        raise ProjectRejected("project_entry_count_exceeded")
                    path = prefix + entry.name
                    validate_project_path(path)
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(entry.name, _DIR_FLAGS, dir_fd=directory)
                        try:
                            visit(child, path + "/")
                        finally:
                            os.close(child)
                    else:
                        if len(files) >= MAX_PROJECT_FILES:
                            raise ProjectRejected("project_file_count_exceeded")
                        _regular(info)
                        if total_bytes + info.st_size > MAX_PROJECT_BYTES:
                            raise ProjectRejected("project_size_exceeded")
                        item = _read(directory, entry.name, path)
                        total_bytes += len(item.content)
                        if total_bytes > MAX_PROJECT_BYTES:
                            raise ProjectRejected("project_size_exceeded")
                        files.append(item)

        try:
            visit(self._fd, "")
        except OSError:
            raise ProjectRejected("project_filesystem_rejected") from None
        return ProjectSnapshot(revision, tuple(sorted(files, key=lambda item: item.path)))
