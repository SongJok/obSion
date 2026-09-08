"""合成 Git 对象验证；不克隆、不提交 ref、不读取宿主配置或运行项目代码。"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import replace
from uuid import UUID

import pytest

from obsion.sandbox import git_integrity
from obsion.sandbox.git_integrity import MAX_COMMIT_BYTES, verify_git_snapshot
from obsion.sandbox.project import ProjectFile, ProjectRejected, ProjectRevision, ProjectSnapshot


def object_id(kind, content, algorithm="sha1"):
    return hashlib.new(
        algorithm, kind + b" " + str(len(content)).encode() + b"\0" + content
    ).hexdigest()


def commit(tree, *, suffix=b"", message=b"synthetic commit\n"):
    return (
        b"tree " + tree.encode() + b"\n"
        b"author Synthetic <synthetic@example.invalid> 0 +0000\n"
        b"committer Synthetic <synthetic@example.invalid> 0 +0000\n" + suffix + b"\n" + message
    )


def source(raw, tree, files, algorithm="sha1"):
    return ProjectSnapshot(
        ProjectRevision(
            UUID(int=1),
            UUID(int=2),
            UUID(int=3),
            UUID(int=4),
            object_id(b"commit", raw, algorithm),
            tree,
        ),
        tuple(files),
    )


def simple_source(*, algorithm="sha1", content=b"source\r\n", executable=False):
    file = ProjectFile("file.txt", content, executable)
    blob = bytes.fromhex(object_id(b"blob", content, algorithm))
    tree = object_id(
        b"tree", (b"100755" if executable else b"100644") + b" file.txt\0" + blob, algorithm
    )
    raw = commit(tree)
    return source(raw, tree, (file,), algorithm), raw


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
@pytest.mark.parametrize("executable", [False, True])
@pytest.mark.parametrize("content", [b"", b"source\r\n", "中文\n无末尾换行".encode()])
def test_byte_exact_files_and_modes(algorithm, executable, content):
    snapshot, raw = simple_source(algorithm=algorithm, content=content, executable=executable)
    assert verify_git_snapshot(snapshot, raw_commit=raw) is None


def test_known_empty_git_tree():
    tree = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
    raw = commit(tree)
    verify_git_snapshot(source(raw, tree, ()), raw_commit=raw)


@pytest.mark.parametrize(
    "change",
    [
        lambda files: (),
        lambda files: (*files, ProjectFile("extra", b"")),
        lambda files: (replace(files[0], path="renamed.txt"),),
        lambda files: (replace(files[0], executable=True),),
        lambda files: (replace(files[0], content=b"source\n"),),
        lambda files: (replace(files[0], content=b"source\r\n\n"),),
    ],
)
def test_missing_extra_renamed_modified_or_wrong_mode_is_rejected(change):
    snapshot, raw = simple_source()
    with pytest.raises(ProjectRejected, match="^project_git_tree_mismatch$"):
        verify_git_snapshot(replace(snapshot, files=change(snapshot.files)), raw_commit=raw)


def test_commit_or_tree_pin_mismatch_is_rejected():
    snapshot, raw = simple_source()
    for changed in (raw + b"x", raw.replace(b"0 +0000", b"1 +0000")):
        with pytest.raises(ProjectRejected, match="^project_git_commit_mismatch$"):
            verify_git_snapshot(snapshot, raw_commit=changed)
    snapshot = replace(snapshot, revision=replace(snapshot.revision, tree_id="0" * 40))
    with pytest.raises(ProjectRejected, match="^project_git_tree_mismatch$"):
        verify_git_snapshot(snapshot, raw_commit=raw)


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw.replace(b"\n\n", b"\n"),
        lambda raw: raw + b"\x00",
        lambda raw: raw.replace(b"\n", b"\r\n"),
        lambda raw: raw.replace(b"\nauthor ", b"\n author "),
        lambda raw: raw.replace(b"\ncommitter ", b"\n committer "),
        lambda raw: raw.replace(b"\ncommitter ", b"\nauthor "),
        lambda raw: raw.replace(b"\nauthor ", b"\ncommitter "),
        lambda raw: raw.replace(b"\n\n", b"\ntree " + b"a" * 40 + b"\n\n"),
        lambda raw: raw.replace(b"\n\n", b"\nAuthor invalid\n\n"),
        lambda raw: raw.replace(b"\n\n", b"\ngpgsig \n\n"),
        lambda raw: raw.replace(b"\n\n", b"\nunknown\n\n"),
        lambda raw: raw.replace(b"\nauthor ", b"\nparent " + b"a" * 7 + b"\nauthor "),
        lambda raw: raw.replace(b"\nauthor ", b"\nparent " + b"A" * 40 + b"\nauthor "),
        lambda raw: raw.replace(b"\n\n", b"\nparent " + b"a" * 40 + b"\n\n"),
        lambda raw: raw.replace(b"\nauthor ", (b"\nparent " + b"a" * 40) * 2 + b"\nauthor "),
    ],
)
def test_matching_hash_does_not_make_malformed_commit_valid(change):
    snapshot, raw = simple_source()
    raw = change(raw)
    with pytest.raises(ProjectRejected, match="^project_git_commit_invalid$"):
        verify_git_snapshot(source(raw, snapshot.revision.tree_id, snapshot.files), raw_commit=raw)


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
def test_merge_parents_and_multiline_headers_do_not_normalize_raw_commit(algorithm):
    snapshot, _ = simple_source(algorithm=algorithm)
    raw = commit(
        snapshot.revision.tree_id,
        suffix=(
            b"encoding UTF-8\ngpgsig synthetic\n continuation\n \n"
            b"mergetag synthetic\n tree ignored\n"
        ),
        message=b"tree in message is not a header\n",
    )
    raw = raw.replace(
        b"\nauthor ",
        b"\nparent "
        + b"a" * len(snapshot.revision.tree_id)
        + b"\nparent "
        + b"b" * len(snapshot.revision.tree_id)
        + b"\nauthor ",
    )
    verify_git_snapshot(
        source(raw, snapshot.revision.tree_id, snapshot.files, algorithm), raw_commit=raw
    )


@pytest.mark.parametrize(
    "raw", [None, "not bytes", bytearray(b"abc"), b"", b"x" * (MAX_COMMIT_BYTES + 1)]
)
def test_raw_commit_type_and_budget_checked_before_hashing(raw, monkeypatch):
    snapshot, _ = simple_source()
    monkeypatch.setattr(git_integrity, "_object_id", lambda *a, **k: pytest.fail("late bound"))
    with pytest.raises(ProjectRejected, match="^project_git_commit_size_invalid$"):
        verify_git_snapshot(snapshot, raw_commit=raw)


@pytest.mark.parametrize(
    "limit,value,reason",
    [
        ("MAX_PROJECT_FILES", 0, "project_file_count_exceeded"),
        ("MAX_PROJECT_BYTES", 1, "project_size_exceeded"),
        ("MAX_PROJECT_ENTRIES", 0, "project_entry_count_exceeded"),
        ("MAX_MANIFEST_BYTES", 1, "project_manifest_size_exceeded"),
    ],
)
def test_snapshot_budgets_are_rechecked_before_hashing(limit, value, reason, monkeypatch):
    from obsion.sandbox import project

    snapshot, raw = simple_source()
    monkeypatch.setattr(project, limit, value)
    monkeypatch.setattr(git_integrity, "_object_id", lambda *a, **k: pytest.fail("late bound"))
    with pytest.raises(ProjectRejected, match=f"^{reason}$"):
        verify_git_snapshot(snapshot, raw_commit=raw)


def test_commit_at_byte_limit_and_snapshot_type_validation():
    snapshot, raw = simple_source()
    raw += b"x" * (MAX_COMMIT_BYTES - len(raw))
    verify_git_snapshot(source(raw, snapshot.revision.tree_id, snapshot.files), raw_commit=raw)
    with pytest.raises(ProjectRejected, match="^project_git_snapshot_invalid$"):
        verify_git_snapshot(snapshot.manifest(), raw_commit=raw)


@pytest.mark.parametrize("mode", [b"120000", b"160000"])
def test_symlink_and_gitlink_cannot_be_silently_treated_as_plain_files(mode):
    file = ProjectFile("file.txt", b"target")
    oid = bytes.fromhex(object_id(b"blob", file.content))
    tree = object_id(b"tree", mode + b" file.txt\0" + oid)
    raw = commit(tree)
    with pytest.raises(ProjectRejected, match="^project_git_tree_mismatch$"):
        verify_git_snapshot(source(raw, tree, (file,)), raw_commit=raw)


def test_filtering_reserved_file_cannot_pass_complete_tree_check():
    tree = object_id(b"tree", b"100644 .env.example\0" + bytes.fromhex(object_id(b"blob", b"")))
    raw = commit(tree)
    with pytest.raises(ProjectRejected, match="^project_git_tree_mismatch$"):
        verify_git_snapshot(source(raw, tree, ()), raw_commit=raw)


def test_lfs_pointer_is_not_presented_as_complete_project_content():
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"a" * 64 + b"\nsize 1\n"
    snapshot, raw = simple_source(content=pointer)
    with pytest.raises(ProjectRejected, match="^project_git_lfs_unsupported$"):
        verify_git_snapshot(snapshot, raw_commit=raw)


def test_error_does_not_echo_commit_message_or_source():
    marker = b"synthetic-private-message"
    snapshot, raw = simple_source(content=marker)
    with pytest.raises(ProjectRejected) as caught:
        verify_git_snapshot(snapshot, raw_commit=raw + marker)
    assert marker.decode() not in str(caught.value)
    assert marker.decode() not in repr(caught.value)


def test_integrity_check_is_explicitly_not_repository_or_workspace_authorization():
    snapshot, raw = simple_source()
    changed = replace(snapshot, revision=replace(snapshot.revision, repository_id=UUID(int=9)))
    # Git 对象不包含组织/Workspace/Connector 身份；上游必须独立授权，不能把 hash 当许可。
    assert verify_git_snapshot(changed, raw_commit=raw) is None


@pytest.mark.parametrize("algorithm", ["sha1", "sha256"])
def test_independent_git_plumbing_agrees_for_nested_tree_sorting_and_modes(tmp_path, algorithm):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git executable required for independent object-format validation")
    root = tmp_path.resolve()
    template = root / "empty-template"
    template.mkdir()
    repo = root / "objects.git"
    env = {
        "PATH": os.path.dirname(git),
        "HOME": str(root),
        "XDG_CONFIG_HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CEILING_DIRECTORIES": str(root.parent),
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }

    def run(*args, data=None):
        # 固定 Git plumbing，仅写本测试创建的空 bare 对象库；无 ref、远端、过滤器或 hook。
        completed = subprocess.run(  # noqa: S603 - 固定本地 Git 参数和合成对象
            [git, "-c", "core.hooksPath=" + str(template), *args],
            input=data,
            cwd=root,
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode()
        return completed.stdout.strip().decode()

    run("init", "--bare", "--template=" + str(template), "--object-format=" + algorithm, str(repo))
    files = (
        ProjectFile("a/z/file", b"nested\n"),
        ProjectFile("a.c", b"extension before directory\n"),
        ProjectFile("a0", b"after directory\n"),
        ProjectFile("文档/空 格.md", "中文内容\r\n".encode()),
        ProjectFile("run.sh", b"exit 0\n", True),
        ProjectFile("empty", b""),
        ProjectFile("no-newline", b"end"),
    )
    entries = {
        item.path: (
            "100755" if item.executable else "100644",
            run(
                "--git-dir=" + str(repo),
                "hash-object",
                "--no-filters",
                "-w",
                "--stdin",
                data=item.content,
            ),
        )
        for item in files
    }

    def tree(prefix):
        records = []
        children = sorted(
            {name[len(prefix) :].split("/")[0] for name in entries if name.startswith(prefix)}
        )
        for child in reversed(children):
            path = prefix + child
            if path in entries:
                mode, oid = entries[path]
                kind = "blob"
            else:
                mode, oid, kind = "040000", tree(path + "/"), "tree"
            records.append(f"{mode} {kind} {oid}\t{child}\0".encode())
        # mktree 独立解析、排序、编码并 hash；不复用生产 tree 构造代码。
        return run("--git-dir=" + str(repo), "mktree", "-z", data=b"".join(records))

    tree_id = tree("")
    raw = commit(tree_id)
    commit_id = run("--git-dir=" + str(repo), "hash-object", "-t", "commit", "--stdin", data=raw)
    snapshot = source(raw, tree_id, files, algorithm)
    assert snapshot.revision.commit_id == commit_id
    verify_git_snapshot(snapshot, raw_commit=raw)
    verify_git_snapshot(replace(snapshot, files=files[::-1]), raw_commit=raw)
    assert list((repo / "refs/heads").iterdir()) == []
