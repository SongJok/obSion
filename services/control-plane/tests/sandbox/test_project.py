"""合成项目的真实文件/ZIP/补丁回归，不读取部署配置或执行项目代码。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import struct
import subprocess
import zipfile
from dataclasses import replace
from uuid import UUID

import pytest

from obsion.sandbox.contracts import SandboxCommand
from obsion.sandbox.delivery import CommandObservation, ProjectDelivery, create_patch
from obsion.sandbox.project import (
    MAX_ARCHIVE_BYTES,
    MAX_FILE_BYTES,
    MAX_PROJECT_BYTES,
    MAX_PROJECT_FILES,
    ProjectFile,
    ProjectRejected,
    ProjectRevision,
    ProjectSnapshot,
    validate_project_path,
)
from obsion.sandbox.workspace import ProjectWorkspace

REVISION = ProjectRevision(UUID(int=1), UUID(int=2), UUID(int=3), UUID(int=4), "a" * 40, "b" * 40)


def snapshot(*files):
    return ProjectSnapshot(REVISION, tuple(files))


def decode(data):
    return ProjectSnapshot.from_archive(data, expected_sha256=hashlib.sha256(data).hexdigest())


def archive(entries, *, manifest=None, mutate_info=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as output:
        if manifest is not None:
            entries = [("manifest.json", json.dumps(manifest).encode()), *entries]
        for name, data in entries:
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            if mutate_info:
                mutate_info(info)
            output.writestr(info, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "../escape",
        "a/../b",
        "a//b",
        "./x",
        "a/",
        "x\\y",
        "C:/x",
        "a\x00b",
        "a\nb",
        "a\tb",
        'a"b',
        "a\u202eb",
        "e\u0301.py",
        "file.",
        "file ",
        "NUL",
        "aux.txt",
        "COM1.md",
        "中" * 86,
        "a/" * 32 + "b",
    ],
)
def test_project_path_rejects_ambiguous_and_nonportable_names(path):
    with pytest.raises(ProjectRejected):
        validate_project_path(path)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.example",
        "src/.ENV.local",
        ".git/config",
        ".GiT",
        ".ssh/id_ed25519",
        ".aws/credentials",
        ".azure/token.json",
        ".config/dws/login",
        ".dws/auth",
        "dws/config",
        ".kube/config",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        "id_rsa",
        "cert.pem",
        "cert.KEY",
        "credentials.json",
        "lib/secret.p12",
    ],
)
def test_project_rejects_credential_paths_even_when_content_is_empty(path):
    with pytest.raises(ProjectRejected, match="path_reserved"):
        ProjectFile(path, b"")


@pytest.mark.parametrize(
    "path", ["src/中文.py", "space dir/file name.txt", ".github/workflows/test.yml"]
)
def test_project_accepts_source_paths(path):
    assert ProjectFile(path, b"# source\n").path == path


@pytest.mark.parametrize(
    "field,value",
    [
        ("commit_id", "main"),
        ("commit_id", "a" * 7),
        ("commit_id", "A" * 40),
        ("tree_id", "b" * 64),
        ("organization_id", UUID(int=0)),
        ("repository_id", "caller-name"),
    ],
)
def test_project_revision_requires_exact_hash_and_identifiers(field, value):
    with pytest.raises(ProjectRejected):
        replace(REVISION, **{field: value})


def test_sha256_git_revision_supported_without_resolving_refs():
    revision = replace(REVISION, commit_id="a" * 64, tree_id="b" * 64)
    assert ProjectRevision.from_wire(revision.to_wire()) == revision


@pytest.mark.parametrize("content", [b"\xff", b"x\x00y", b"x" * (MAX_FILE_BYTES + 1)])
def test_project_rejects_binary_and_oversize(content):
    with pytest.raises(ProjectRejected):
        ProjectFile("a.txt", content)


def test_secret_detection_does_not_skip_test_files_or_echo_content():
    marker = "synthetic-secret-" + "z" * 32
    with pytest.raises(ProjectRejected) as caught:
        ProjectFile("tests/fixture.txt", ("OBSION_DINGTALK_APP_SECRET=" + marker).encode())
    assert str(caught.value) == "project_secret_detected"
    assert marker not in repr(caught.value)
    assert marker not in repr(ProjectFile("source.txt", b"ordinary source"))


@pytest.mark.parametrize(
    "paths",
    [
        ["same", "same"],
        ["A", "a"],
        ["file", "file/child"],
        ["File/x", "file/y"],
        ["Dir/sub/x", "Dir/Sub/y"],
    ],
)
def test_project_rejects_duplicate_file_directory_and_case_collisions(paths):
    with pytest.raises(ProjectRejected, match="collision"):
        snapshot(*(ProjectFile(path, b"") for path in paths))


def test_file_count_and_aggregate_bytes_are_bounded():
    with pytest.raises(ProjectRejected, match="count"):
        snapshot(*(ProjectFile(str(index), b"") for index in range(MAX_PROJECT_FILES + 1)))
    with pytest.raises(ProjectRejected, match="size"):
        snapshot(
            *(
                ProjectFile(str(index), b"x" * MAX_FILE_BYTES)
                for index in range(MAX_PROJECT_BYTES // MAX_FILE_BYTES + 1)
            )
        )


def test_archive_is_deterministic_content_and_mode_verified():
    source = snapshot(
        ProjectFile("src/中文.py", "print('你好')\n".encode(), True),
        ProjectFile("README.md", b"read me\n"),
    )
    data = source.to_archive()
    assert data == replace(source, files=source.files[::-1]).to_archive()
    restored = decode(data)
    assert restored.manifest() == source.manifest()
    assert restored.fingerprint == source.fingerprint
    assert {file.path: file for file in restored.files} == {
        file.path: file for file in source.files
    }
    assert len(data) <= MAX_ARCHIVE_BYTES
    with pytest.raises(ProjectRejected, match="checksum_mismatch"):
        ProjectSnapshot.from_archive(data, expected_sha256="0" * 64)


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m.update(version=True),
        lambda m: m.update(extra="ignored"),
        lambda m: m["revision"].update(commit_id="main"),
        lambda m: m["revision"].update(organization_id="not-a-uuid"),
        lambda m: m["files"][0].update(path="../escape"),
        lambda m: m["files"][0].update(size=True),
        lambda m: m["files"][0].update(size=20),
        lambda m: m["files"][0].update(sha256="0" * 64),
        lambda m: m["files"][0].update(executable=True),
        lambda m: m["files"].append(dict(m["files"][0])),
    ],
)
def test_untrusted_archive_manifest_cannot_lie_about_content_or_identity(change):
    manifest = snapshot(ProjectFile("source", b"content")).manifest()
    change(manifest)
    with pytest.raises(ProjectRejected):
        decode(archive([("files/source", b"content")], manifest=manifest))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda i: setattr(i, "external_attr", (stat.S_IFLNK | 0o777) << 16),
        lambda i: setattr(i, "external_attr", (stat.S_IFIFO | 0o644) << 16),
        lambda i: setattr(i, "compress_type", zipfile.ZIP_DEFLATED),
        lambda i: setattr(i, "comment", b"hidden bytes"),
        lambda i: setattr(i, "create_system", 0),
    ],
)
def test_archive_rejects_links_special_files_compression_and_metadata(mutation):
    with pytest.raises(ProjectRejected):
        decode(archive([], manifest=snapshot().manifest(), mutate_info=mutation))


def test_archive_rejects_extra_duplicate_and_missing_entries():
    manifest = snapshot(ProjectFile("source", b"content")).manifest()
    for entries in (
        [("files/source", b"content"), ("files/extra", b"hidden")],
        [],
    ):
        with pytest.raises(ProjectRejected):
            decode(archive(entries, manifest=manifest))
    with pytest.warns(UserWarning, match="Duplicate"):
        data = archive([("files/source", b"content")] * 2, manifest=manifest)
    with pytest.raises(ProjectRejected):
        decode(data)


def test_archive_duplicate_json_keys_and_truncation_rejected():
    data = archive([("manifest.json", b'{"version":1,"version":1,"files":[]}')])
    with pytest.raises(ProjectRejected):
        decode(data)
    with pytest.raises(ProjectRejected):
        decode(snapshot().to_archive()[:-25])


def test_archive_hidden_prefix_suffix_and_nul_names_are_rejected():
    canonical = snapshot(ProjectFile("source", b"content")).to_archive()
    for data in (b"hidden-prefix" + canonical, canonical + b"hidden-suffix"):
        with pytest.raises(ProjectRejected):
            decode(data)
    data = canonical.replace(b"files/source", b"files/sour\x00e")
    with pytest.raises(ProjectRejected):
        decode(data)


def test_zip_directory_count_checked_before_materializing_entries(monkeypatch):
    import obsion.sandbox.project as project

    manifest = snapshot().manifest()
    entries = [(f"files/{index}", b"") for index in range(MAX_PROJECT_FILES + 1)]
    data = bytearray(archive(entries, manifest=manifest))
    # 隐瞒 EOCD 条目计数，不应让 ZipFile 先分配大量 ZipInfo。
    struct.pack_into("<HH", data, len(data) - 14, 1, 1)
    monkeypatch.setattr(project.zipfile, "ZipFile", lambda *a, **kw: pytest.fail("late bound"))
    with pytest.raises(ProjectRejected, match="archive_invalid"):
        decode(bytes(data))


def test_manifest_and_entry_budgets_are_enforced_before_archive():
    with pytest.raises(ProjectRejected, match="entry_count"):
        snapshot(*(ProjectFile(f"{index}/a/b/c/file", b"") for index in range(500)))
    prefix = "/".join(["中" * 80] * 8)
    with pytest.raises(ProjectRejected, match="manifest_size"):
        snapshot(*(ProjectFile(f"{prefix}/{index}", b"") for index in range(500)))


def test_workspace_materialize_read_modify_capture_and_no_overwrite(tmp_path):
    base = snapshot(ProjectFile("src/main.py", b"print(1)\n"), ProjectFile("old.txt", b"old"))
    root = tmp_path.resolve()
    with ProjectWorkspace(root) as workspace:
        workspace.materialize(decode(base.to_archive()))
        assert workspace.read_file("src/main.py").content == b"print(1)\n"
        assert workspace.capture(REVISION).fingerprint == base.fingerprint
        with pytest.raises(ProjectRejected, match="not_empty"):
            workspace.materialize(base)
        workspace.write_file(ProjectFile("src/main.py", b"print(2)\n", True))
        workspace.remove_file("old.txt")
        workspace.write_file(ProjectFile("docs/new.md", "新增文档\n".encode()))
        result = workspace.capture(REVISION)
    assert (
        result.manifest()
        == snapshot(
            ProjectFile("src/main.py", b"print(2)\n", True),
            ProjectFile("docs/new.md", "新增文档\n".encode()),
        ).manifest()
    )
    with pytest.raises(ProjectRejected, match="closed"):
        workspace.read_file("src/main.py")


def test_root_and_ancestor_symlinks_rejected(tmp_path):
    root = tmp_path.resolve()
    (root / "real").mkdir()
    (root / "real" / "sub").mkdir()
    (root / "link").symlink_to(root / "real", target_is_directory=True)
    for path in (root / "link", root / "link/sub"):
        with pytest.raises(ProjectRejected, match="root_invalid"):
            ProjectWorkspace(path)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "directory_symlink"])
def test_untrusted_workspace_links_and_devices_never_read_or_written(tmp_path, kind):
    root = tmp_path.resolve()
    outside = root / "outside.txt"
    outside.write_bytes(b"outside-marker")
    working = root / "project"
    working.mkdir()
    target = working / "unsafe"
    if kind == "symlink":
        target.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, target)
    elif kind == "fifo":
        os.mkfifo(target)
    else:
        target.symlink_to(root, target_is_directory=True)
    with ProjectWorkspace(working) as workspace:
        with pytest.raises(ProjectRejected):
            workspace.read_file("unsafe")
        with pytest.raises(ProjectRejected):
            workspace.write_file(ProjectFile("unsafe", b"changed"))
        with pytest.raises(ProjectRejected):
            workspace.remove_file("unsafe")
        with pytest.raises(ProjectRejected):
            workspace.capture(REVISION)
        if kind == "directory_symlink":
            with pytest.raises(ProjectRejected):
                workspace.write_file(ProjectFile("unsafe/outside.txt", b"changed"))
    assert outside.read_bytes() == b"outside-marker"


def test_capture_rejects_secrets_and_oversize_generated_output(tmp_path):
    root = tmp_path.resolve()
    with ProjectWorkspace(root) as workspace:
        (root / ".env").write_text("synthetic")
        with pytest.raises(ProjectRejected, match="reserved"):
            workspace.capture(REVISION)
        (root / ".env").unlink()
        (root / "huge").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        with pytest.raises(ProjectRejected, match="size"):
            workspace.capture(REVISION)


def test_read_rejects_mode_and_changes_during_read(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "file").write_bytes(b"original")
    (root / "file").chmod(0o666)
    with ProjectWorkspace(root) as workspace:
        with pytest.raises(ProjectRejected, match="mode"):
            workspace.read_file("file")
        (root / "file").chmod(0o644)
        original = os.fstat
        calls = 0

        def mutate(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                (root / "file").write_bytes(b"different")
            return original(fd)

        monkeypatch.setattr(os, "fstat", mutate)
        with pytest.raises(ProjectRejected, match="changed"):
            workspace.read_file("file")


def test_failed_write_preserves_old_file_and_removes_only_owned_temporary(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    (root / "file").write_bytes(b"original")

    def reject(*args, **kwargs):
        raise OSError("synthetic-private-path")

    with ProjectWorkspace(root) as workspace:
        monkeypatch.setattr(os, "replace", reject)
        with pytest.raises(ProjectRejected) as caught:
            workspace.write_file(ProjectFile("file", b"new"))
    assert str(caught.value) == "project_filesystem_rejected"
    assert (root / "file").read_bytes() == b"original"
    assert sorted(item.name for item in root.iterdir()) == ["file"]


@pytest.mark.parametrize("swap", [False, True], ids=["files", "file-directory-swap"])
def test_real_git_apply_reconstructs_content_modes_and_no_newline(tmp_path, swap):
    git = shutil.which("git")
    if git is None:
        pytest.skip("git executable required for real patch application")
    base = snapshot(
        ProjectFile("src/main.py", b"print(1)\n"),
        ProjectFile("old.txt", b"remove me"),
        ProjectFile("no-newline.txt", b"before"),
        ProjectFile("mode.sh", b"exit 0\n"),
        ProjectFile("empty-deleted", b""),
        ProjectFile("crlf.txt", b"before\r\n"),
    )
    result = snapshot(
        ProjectFile("src/main.py", b"print(2)\n"),
        ProjectFile("new empty", b""),
        ProjectFile("文档/你好 world.md", "中文内容\n".encode()),
        ProjectFile("no-newline.txt", b"after"),
        ProjectFile("mode.sh", b"exit 0\n", True),
        ProjectFile("crlf.txt", b"after\r\n"),
    )
    if swap:
        base = snapshot(ProjectFile("to-dir", b"before"), ProjectFile("to-file/old", b"old"))
        result = snapshot(ProjectFile("to-dir/new", b"new"), ProjectFile("to-file", b"after"))
    patch = create_patch(base, result)
    root = tmp_path.resolve()
    with ProjectWorkspace(root) as workspace:
        workspace.materialize(base)
    env = {
        "PATH": os.path.dirname(git),
        "HOME": str(root),
        "XDG_CONFIG_HOME": str(root),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CEILING_DIRECTORIES": str(root.parent),
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
    }
    # 仅在临时合成目录执行 Git 内置 apply；不 init/commit、不联网、不执行项目代码。
    for args in (["--check"], []):
        completed = subprocess.run(  # noqa: S603 - 固定 git apply 参数和合成测试补丁
            [git, "apply", "--no-index", "--whitespace=nowarn", *args, "-"],
            input=patch,
            cwd=root,
            env=env,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode()
    with ProjectWorkspace(root) as workspace:
        assert workspace.capture(REVISION).manifest() == result.manifest()
    assert create_patch(base, base) == b""


def test_delivery_rejects_known_secret_in_reproduction_command():
    marker = "synthetic-secret-" + "z" * 32
    command = SandboxCommand("/usr/bin/printf", ("OBSION_DINGTALK_APP_SECRET=" + marker,))
    with pytest.raises(ProjectRejected, match="secret_detected"):
        CommandObservation(UUID(int=5), command, "exited", 0, UUID(int=6))


def test_patch_repeated_lines_has_hard_output_budget(monkeypatch):
    from obsion.sandbox import delivery

    base = snapshot(ProjectFile("a", b"a\n" * 1000))
    result = snapshot(ProjectFile("a", b"b\n" * 1000))
    monkeypatch.setattr(delivery, "MAX_PATCH_BYTES", 1024)
    with pytest.raises(ProjectRejected, match="patch_size"):
        create_patch(base, result)


def test_delivery_stays_unverified_even_if_commands_exit_zero():
    base = snapshot(ProjectFile("a", b"before"))
    result = snapshot(ProjectFile("a", b"after"))
    observation = CommandObservation(
        UUID(int=5), SandboxCommand("/usr/bin/true"), "exited", 0, UUID(int=6)
    )
    delivery = ProjectDelivery(base, result, (observation,))
    manifest = delivery.manifest()
    assert manifest["revision"]["commit_id"] == REVISION.commit_id
    assert manifest["patch_sha256"] == hashlib.sha256(delivery.patch).hexdigest()
    assert manifest["verification"] == "NOT_EVALUATED"
    assert manifest["commands"][0]["exit_code"] == 0
    assert manifest["unresolved"] == ["independent_verification_required"]
    with pytest.raises(ProjectRejected, match="duplicate"):
        ProjectDelivery(base, result, (observation, observation))
    with pytest.raises(ProjectRejected, match="revision_mismatch"):
        create_patch(base, replace(result, revision=replace(REVISION, commit_id="c" * 40)))


@pytest.mark.parametrize(
    "outcome,exit_code",
    [
        ("unknown", 0),
        ("exited", None),
        ("exited", True),
        ("timeout", 0),
        ("cancelled", 0),
        ("exited", 256),
        ("success", 0),
    ],
)
def test_observation_cannot_invent_exit_codes(outcome, exit_code):
    with pytest.raises(ProjectRejected):
        CommandObservation(
            UUID(int=5), SandboxCommand("/usr/bin/true"), outcome, exit_code, UUID(int=6)
        )
