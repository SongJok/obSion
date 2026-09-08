"""有界 Git 内容完整性检查，不证明仓库身份、应用 scopes 或调用者授权。

仅处理已经进入可信边界的字节；不解析 ref、不联网、不运行 Git/过滤器，
不把校验成功标记为授权或项目验证成功。来源获取与访问控制由 Gateway 负责。
"""

from __future__ import annotations

import hashlib
import re

from obsion.sandbox.project import ProjectRejected, ProjectSnapshot

MAX_COMMIT_BYTES = 262144
_LFS_PREFIX = b"version https://git-lfs.github.com/spec/"


def _object_id(kind: bytes, content: bytes, *, sha256: bool) -> bytes:
    # SHA-1 仅兼容 Git 对象格式，不用作授权、签名或防碰撞安全凭证。
    digest = hashlib.sha256() if sha256 else hashlib.sha1(usedforsecurity=False)
    digest.update(kind + b" " + str(len(content)).encode("ascii") + b"\x00")
    digest.update(content)
    return digest.digest()


def _validate_commit(raw: bytes, tree_id: str) -> None:
    headers, separator, _ = raw.partition(b"\n\n")
    if not separator or b"\x00" in raw or b"\r" in headers:
        raise ProjectRejected("project_git_commit_invalid")
    lines = headers.split(b"\n")
    if lines[0] != b"tree " + tree_id.encode("ascii"):
        raise ProjectRejected("project_git_tree_mismatch")
    seen: set[bytes] = set()
    parents: set[bytes] = set()
    previous = b"tree"
    for line in lines[1:]:
        if line.startswith(b" "):
            if previous in {b"tree", b"parent", b"author", b"committer"}:
                raise ProjectRejected("project_git_commit_invalid")
            continue
        key, space, value = line.partition(b" ")
        if not space or not value or not re.fullmatch(rb"[a-z][a-z0-9-]*", key):
            raise ProjectRejected("project_git_commit_invalid")
        if key == b"tree":
            raise ProjectRejected("project_git_commit_invalid")
        if key == b"parent":
            if (
                seen.intersection({b"author", b"committer"})
                or len(value) != len(tree_id)
                or not re.fullmatch(rb"[0-9a-f]+", value)
                or value in parents
            ):
                raise ProjectRejected("project_git_commit_invalid")
            parents.add(value)
        elif key in {b"author", b"committer"}:
            if key in seen or (key == b"committer" and b"author" not in seen):
                raise ProjectRejected("project_git_commit_invalid")
        seen.add(key)
        previous = key
    if not {b"author", b"committer"}.issubset(seen):
        raise ProjectRejected("project_git_commit_invalid")


def _tree_id(snapshot: ProjectSnapshot, *, sha256: bool) -> str:
    directories: dict[str, list[tuple[bytes, bytes, bytes]]] = {"": []}
    for item in snapshot.files:
        if item.content.startswith(_LFS_PREFIX):
            raise ProjectRejected("project_git_lfs_unsupported")
        parts = item.path.split("/")
        for count in range(1, len(parts)):
            directories.setdefault("/".join(parts[:count]), [])
        parent = "/".join(parts[:-1])
        directories[parent].append(
            (
                parts[-1].encode("utf-8"),
                b"100755" if item.executable else b"100644",
                _object_id(b"blob", item.content, sha256=sha256),
            )
        )
    # 深度、条目和字节已由 ProjectSnapshot 限制；从叶到根，无递归或额外对象包。
    for path in sorted(directories, key=lambda value: (value.count("/"), len(value)), reverse=True):
        entries = directories[path]
        # Git 排序按原始文件名字节，目录比较时追加 '/'，不是按完整路径排序。
        entries.sort(key=lambda entry: entry[0] + (b"/" if entry[1] == b"40000" else b""))
        content = b"".join(mode + b" " + name + b"\x00" + oid for name, mode, oid in entries)
        oid = _object_id(b"tree", content, sha256=sha256)
        if not path:
            return oid.hex()
        parent, _, name = path.rpartition("/")
        directories[parent].append((name.encode("utf-8"), b"40000", oid))
    raise ProjectRejected("project_git_tree_mismatch")


def verify_git_snapshot(snapshot: ProjectSnapshot, *, raw_commit: bytes) -> None:
    """检查 commit → tree → 全部文件字节/mode；成功不授予任何权限。

    raw_commit 是解码后的原始 commit 对象内容，不含 Git 对象头或压缩封装。
    不接受规范化 JSON 摘要代替原始字节，不改写换行、过滤文件或展开 LFS。
    父提交可不在当前副本中；签名、父图有效性和 Git fsck 不属于本函数保证。
    """
    if type(snapshot) is not ProjectSnapshot:
        raise ProjectRejected("project_git_snapshot_invalid")
    if type(raw_commit) is not bytes or not 0 < len(raw_commit) <= MAX_COMMIT_BYTES:
        raise ProjectRejected("project_git_commit_size_invalid")
    # 再用既有契约检查预算与路径冲突，不增加放宽限制的 Git 专用副本类型。
    ProjectSnapshot(snapshot.revision, snapshot.files)
    sha256 = len(snapshot.revision.commit_id) == 64
    if _object_id(b"commit", raw_commit, sha256=sha256).hex() != snapshot.revision.commit_id:
        raise ProjectRejected("project_git_commit_mismatch")
    _validate_commit(raw_commit, snapshot.revision.tree_id)
    if _tree_id(snapshot, sha256=sha256) != snapshot.revision.tree_id:
        raise ProjectRejected("project_git_tree_mismatch")
