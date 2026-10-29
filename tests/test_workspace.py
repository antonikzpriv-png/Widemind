import concurrent.futures
import hashlib
import os
from pathlib import Path

import pytest

from widemind.tools.contracts import ToolError
from widemind.workspace import DedicatedWorkspace


@pytest.fixture
def workspace(tmp_path):
    with DedicatedWorkspace(tmp_path / "artifacts") as value:
        yield value


def test_real_bytes_atomic_replace_listing_delete(workspace):
    artifact = workspace.write_bytes("customer/report.txt", b"real deliverable")
    assert artifact == {"path": "customer/report.txt", "sha256": hashlib.sha256(b"real deliverable").hexdigest(),
                        "size_bytes": 16, "media_type": "text/plain"}
    assert workspace.read_bytes(artifact["path"]) == b"real deliverable"
    assert workspace.list_files() == [{"path": artifact["path"], "size_bytes": 16, "media_type": "text/plain"}]
    with pytest.raises(ToolError, match="overwrite"):
        workspace.write_bytes(artifact["path"], b"changed")
    workspace.write_bytes(artifact["path"], b"changed", overwrite=True)
    assert workspace.read_bytes(artifact["path"]) == b"changed"
    assert workspace.delete(artifact["path"])["deleted"] is True
    assert workspace.list_files() == []
    assert not any(item["path"].startswith(".") for item in workspace.list_files())


@pytest.mark.parametrize("path", ["../escape", "/etc/passwd", "a/../escape", "a//b", ".widemind/lock",
                                  "a\\b", "a\x00b", "a/./b", "CON.txt", "x:ads", "a.", "a ", ""])
def test_rejects_unsafe_paths(workspace, path):
    for operation in (lambda: workspace.read_bytes(path), lambda: workspace.write_bytes(path, b"x"),
                      lambda: workspace.delete(path)):
        with pytest.raises(ToolError) as error:
            operation()
        assert error.value.code == "invalid_path"


def test_size_total_count_and_depth_limits(tmp_path):
    with DedicatedWorkspace(tmp_path / "small", max_file_bytes=4, max_total_bytes=6, max_files=2, max_depth=2) as ws:
        ws.write_bytes("a", b"1234")
        with pytest.raises(ToolError):
            ws.write_bytes("oversize", b"12345")
        with pytest.raises(ToolError):
            ws.read_bytes("a", max_bytes=3)
        with pytest.raises(ToolError):
            ws.write_bytes("b", b"123")
        ws.write_bytes("dir/b", b"12")
        with pytest.raises(ToolError):
            ws.write_bytes("c", b"")
        with pytest.raises(ToolError):
            ws.write_bytes("x/y/z", b"")
        ws.write_bytes("a", b"1", overwrite=True)
        assert len(ws.list_files()) == 2


def test_symlink_and_hardlink_targets_never_read_or_modified(workspace, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"outside secret")
    (workspace.root / "link").symlink_to(secret)
    for operation in (lambda: workspace.read_bytes("link"), lambda: workspace.write_bytes("link", b"bad", overwrite=True),
                      lambda: workspace.delete("link"), workspace.list_files):
        with pytest.raises(ToolError):
            operation()
    (workspace.root / "link").unlink()
    os.link(secret, workspace.root / "hardlink")
    for operation in (lambda: workspace.read_bytes("hardlink"), lambda: workspace.write_bytes("hardlink", b"bad", overwrite=True),
                      lambda: workspace.delete("hardlink"), workspace.list_files):
        with pytest.raises(ToolError):
            operation()
    assert secret.read_bytes() == b"outside secret"


def test_symlink_root_and_ancestor_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (tmp_path / "alias").symlink_to(target, target_is_directory=True)
    with pytest.raises(OSError):
        DedicatedWorkspace(tmp_path / "alias")
    with pytest.raises(OSError):
        DedicatedWorkspace(tmp_path / "alias" / "nested")


def test_existing_workspace_must_be_private_and_dedicated(tmp_path):
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    with pytest.raises(ToolError) as error:
        DedicatedWorkspace(root)
    assert error.value.code == "invalid_workspace"
    with pytest.raises(ValueError):
        DedicatedWorkspace(Path("/workspace"))


def test_fifos_and_directories_rejected_without_blocking(workspace):
    os.mkfifo(workspace.root / "pipe")
    with pytest.raises(ToolError):
        workspace.read_bytes("pipe")
    (workspace.root / "folder").mkdir()
    with pytest.raises(ToolError):
        workspace.delete("folder")


def test_quota_serialized_across_workspace_instances(tmp_path):
    root = tmp_path / "shared"
    with DedicatedWorkspace(root, max_files=1) as first, DedicatedWorkspace(root, max_files=1) as second:
        def write(pair):
            workspace, name = pair
            try:
                workspace.write_bytes(name, b"x")
                return True
            except ToolError as error:
                assert error.code == "quota_exceeded"
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            assert sorted(pool.map(write, [(first, "a"), (second, "b")])) == [False, True]
        assert len(first.list_files()) == 1


def test_parent_symlink_swapped_immediately_before_open_is_blocked(workspace, tmp_path, monkeypatch):
    (workspace.root / "nested").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = os.open
    swapped = False

    def raced_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "nested" and not swapped:
            swapped = True
            (workspace.root / "nested").rename(workspace.root / "parked")
            (workspace.root / "nested").symlink_to(outside, target_is_directory=True)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", raced_open)
    with pytest.raises(ToolError):
        workspace.write_bytes("nested/file.txt", b"secret")
    assert list(outside.iterdir()) == []


def test_parent_swap_after_open_keeps_write_on_pinned_directory(workspace, tmp_path, monkeypatch):
    (workspace.root / "nested").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original = os.link

    def raced_link(source, target, **kwargs):
        (workspace.root / "nested").rename(workspace.root / "parked")
        (workspace.root / "nested").symlink_to(outside, target_is_directory=True)
        return original(source, target, **kwargs)

    monkeypatch.setattr(os, "link", raced_link)
    workspace.write_bytes("nested/file.txt", b"safe pinned bytes")
    assert list(outside.iterdir()) == []
    assert (workspace.root / "parked/file.txt").read_bytes() == b"safe pinned bytes"


def test_leaf_symlink_swap_immediately_before_read_is_blocked(workspace, tmp_path, monkeypatch):
    workspace.write_bytes("file.txt", b"initial")
    secret = tmp_path / "secret"
    secret.write_bytes(b"outside secret")
    original = os.open

    def raced_open(path, flags, *args, **kwargs):
        if path == "file.txt":
            (workspace.root / "file.txt").unlink()
            (workspace.root / "file.txt").symlink_to(secret)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", raced_open)
    with pytest.raises(ToolError):
        workspace.read_bytes("file.txt")
    assert secret.read_bytes() == b"outside secret"


def test_no_overwrite_race_is_atomic(workspace, monkeypatch):
    original = os.link

    def raced_link(source, target, **kwargs):
        (workspace.root / "file.txt").write_bytes(b"concurrent winner")
        return original(source, target, **kwargs)

    monkeypatch.setattr(os, "link", raced_link)
    with pytest.raises(ToolError) as error:
        workspace.write_bytes("file.txt", b"loser")
    assert error.value.code == "already_exists"
    assert workspace.read_bytes("file.txt") == b"concurrent winner"


def test_partial_write_failure_preserves_old_bytes_and_cleans_staging(workspace, monkeypatch):
    workspace.write_bytes("file.txt", b"old")
    original = os.write
    calls = 0

    def failed_write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected IO failure")
        return original(fd, data)

    monkeypatch.setattr(os, "write", failed_write)
    with pytest.raises(ToolError):
        workspace.write_bytes("file.txt", b"x" * 100_000, overwrite=True)
    assert workspace.read_bytes("file.txt") == b"old"
    assert [p.name for p in (workspace.root / ".widemind").iterdir()] == ["lock"]


def test_read_growth_is_bounded(workspace, monkeypatch):
    workspace.write_bytes("file.txt", b"1234")
    original = os.read
    grown = False

    def raced_read(fd, size):
        nonlocal grown
        if not grown:
            grown = True
            with (workspace.root / "file.txt").open("ab") as handle:
                handle.write(b"56789")
        return original(fd, size)

    monkeypatch.setattr(os, "read", raced_read)
    with pytest.raises(ToolError) as error:
        workspace.read_bytes("file.txt", max_bytes=4)
    assert error.value.code == "output_limit"
