"""Confined local artifacts, using pinned directory descriptors on POSIX.

This is an API boundary, not an OS sandbox. Never run untrusted code as the
gateway's uid, or place the control database or credentials under this root.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import stat
import threading
import time
import uuid

from .tools.contracts import ToolError


_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_INTERNAL = ".widemind"
_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ -]{0,127}\Z")
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?\Z", re.I)


def _integer(value: int, name: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be an integer between 1 and {maximum}.")
    return value


def validate_workspace_path(path: str, *, max_depth: int = 32, empty: bool = False) -> list[str]:
    """Validate a portable, non-hidden relative artifact path without IO."""
    if not isinstance(path, str) or len(path) > 1024:
        raise ToolError("invalid_path", "Use a bounded relative workspace path.")
    if empty and path == "":
        return []
    parts = path.split("/")
    if len(parts) > max_depth or any(
        not _COMPONENT.fullmatch(part) or part.endswith((".", " "))
        or _RESERVED.fullmatch(part) for part in parts
    ):
        raise ToolError("invalid_path", "Use safe relative names with no hidden files, traversal, or control characters.")
    return parts


class DedicatedWorkspace:
    """Owner-selected dedicated directory; all public file paths are relative.

    A lock serializes quotas across instances/processes using this API. All
    directory traversal uses openat + O_NOFOLLOW; file reads reject hard links,
    devices, FIFOs and sockets. Staging needs at most one extra max-sized file.
    """

    def __init__(self, root: str | Path, *, max_file_bytes: int = 8_000_000,
                 max_total_bytes: int = 64_000_000, max_files: int = 1000,
                 max_depth: int = 16):
        self.max_file_bytes = _integer(max_file_bytes, "max_file_bytes", 128_000_000)
        self.max_total_bytes = _integer(max_total_bytes, "max_total_bytes", 10_000_000_000)
        self.max_files = _integer(max_files, "max_files", 100_000)
        self.max_depth = _integer(max_depth, "max_depth", 32)
        self.root = Path(os.path.abspath(os.fspath(root)))
        if self.root in map(Path, ("/", "/tmp", "/workspace", "/home", "/etc", "/var", "/usr")):
            raise ValueError("Choose a dedicated workspace subdirectory, not a host resource root.")
        self._thread_lock = threading.RLock()
        self._root_fd = self._internal_fd = self._lock_fd = -1
        parent_fd = os.open("/", _DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, _DIRECTORY, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            self._root_fd, parent_fd = parent_fd, -1
            root_info = os.fstat(self._root_fd)
            self._device = root_info.st_dev
            self._private_directory(root_info)
            try:
                os.mkdir(_INTERNAL, mode=0o700, dir_fd=self._root_fd)
            except FileExistsError:
                pass
            self._internal_fd = self._open_directory(self._root_fd, _INTERNAL)
            self._private_directory(os.fstat(self._internal_fd))
            self._lock_fd = os.open("lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    0o600, dir_fd=self._internal_fd)
            self._regular(os.fstat(self._lock_fd))
            with self._locked():
                self._clean_staging()
        except BaseException:
            self.close()
            raise
        finally:
            if parent_fd >= 0:
                os.close(parent_fd)

    def close(self) -> None:
        for name in ("_lock_fd", "_internal_fd", "_root_fd"):
            fd = getattr(self, name, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __del__(self):
        self.close()

    @contextlib.contextmanager
    def _locked(self):
        if not self._thread_lock.acquire(timeout=5):
            raise ToolError("workspace_busy", "Workspace is busy; retry after the current local operation completes.", retryable=True)
        try:
            if self._lock_fd < 0:
                raise ToolError("workspace_closed", "The workspace is closed.")
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ToolError("workspace_busy", "Workspace is locked by another local operation.", retryable=True) from None
                    time.sleep(.01)
            try:
                yield
            except OSError as exc:
                # Never disclose an absolute host path through an OS exception.
                code = "already_exists" if isinstance(exc, FileExistsError) else "workspace_io"
                if isinstance(exc, FileNotFoundError):
                    code = "not_found"
                raise ToolError(code, "Workspace operation failed; check the relative path and file type.") from None
            finally:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            self._thread_lock.release()

    def _parts(self, path: str, *, empty: bool = False) -> list[str]:
        return validate_workspace_path(path, max_depth=self.max_depth, empty=empty)

    def _open_directory(self, parent_fd: int, name: str) -> int:
        fd = os.open(name, _DIRECTORY, dir_fd=parent_fd)
        if os.fstat(fd).st_dev != self._device:
            os.close(fd)
            raise ToolError("invalid_path", "Workspace traversal cannot cross a filesystem mount.")
        return fd

    @contextlib.contextmanager
    def _directory(self, parts: list[str], *, create: bool = False):
        fd = os.dup(self._root_fd)
        try:
            for name in parts:
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                next_fd = self._open_directory(fd, name)
                os.close(fd)
                fd = next_fd
            yield fd
        finally:
            os.close(fd)

    def _regular(self, info: os.stat_result) -> None:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_dev != self._device:
            raise ToolError("invalid_path", "Only ordinary, unlinked workspace files are allowed.")

    @staticmethod
    def _private_directory(info: os.stat_result) -> None:
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ToolError("invalid_workspace", "Choose a dedicated directory owned by the gateway user with mode 0700.")

    def _clean_staging(self) -> None:
        for name in os.listdir(self._internal_fd):
            if re.fullmatch(r"stage-[0-9a-f]{32}", name):
                os.unlink(name, dir_fd=self._internal_fd)

    @staticmethod
    def _artifact(path: str, data: bytes) -> dict:
        return {"path": path, "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "media_type": mimetypes.guess_type(path)[0] or "application/octet-stream"}

    def _inventory(self, parts: list[str] | None = None) -> list[dict]:
        parts = parts or []
        files: list[dict] = []
        entries = 0
        total = 0

        def visit(fd: int, prefix: list[str]) -> None:
            nonlocal entries, total
            for name in sorted(os.listdir(fd)):
                if not prefix and name == _INTERNAL:
                    continue
                self._parts("/".join(prefix + [name]))
                entries += 1
                if entries > self.max_files * self.max_depth:
                    raise ToolError("quota_exceeded", "Workspace directory entry limit exceeded.")
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child_fd = self._open_directory(fd, name)
                    try:
                        visit(child_fd, prefix + [name])
                    finally:
                        os.close(child_fd)
                else:
                    self._regular(info)
                    total += info.st_size
                    files.append({"path": "/".join(prefix + [name]), "size_bytes": info.st_size,
                                  "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream"})
                    if len(files) > self.max_files or total > self.max_total_bytes or info.st_size > self.max_file_bytes:
                        raise ToolError("quota_exceeded", "Workspace file count or byte quota exceeded.")

        with self._directory(parts) as directory_fd:
            visit(directory_fd, parts)
        return files

    def read_bytes(self, path: str, *, max_bytes: int = 8_000_000) -> bytes:
        parts = self._parts(path)
        if type(max_bytes) is not int or max_bytes < 0:
            raise ToolError("invalid_arguments", "max_bytes must be a nonnegative integer.")
        bound = min(max_bytes, self.max_file_bytes)
        with self._locked(), self._directory(parts[:-1]) as directory_fd:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=directory_fd)
            try:
                info = os.fstat(fd)
                self._regular(info)
                if info.st_size > bound:
                    raise ToolError("output_limit", "File exceeds the read byte limit.")
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = os.read(fd, min(65_536, bound - size + 1))
                    if not chunk:
                        return b"".join(chunks)
                    size += len(chunk)
                    if size > bound:
                        raise ToolError("output_limit", "File exceeds the read byte limit.")
                    chunks.append(chunk)
            finally:
                os.close(fd)

    def write_bytes(self, path: str, data: bytes, *, overwrite: bool = False) -> dict:
        parts = self._parts(path)
        if not isinstance(data, bytes) or type(overwrite) is not bool:
            raise ToolError("invalid_arguments", "Supply bytes and an explicit boolean overwrite flag.")
        if len(data) > self.max_file_bytes:
            raise ToolError("quota_exceeded", "File exceeds the workspace file byte limit.")
        with self._locked():
            self._clean_staging()
            inventory = self._inventory()
            old = next((item for item in inventory if item["path"] == path), None)
            if old and not overwrite:
                raise ToolError("already_exists", "The file already exists; overwrite must be explicit.")
            if len(inventory) + (0 if old else 1) > self.max_files or sum(
                item["size_bytes"] for item in inventory
            ) - (old["size_bytes"] if old else 0) + len(data) > self.max_total_bytes:
                raise ToolError("quota_exceeded", "Workspace total byte or file count limit exceeded.")
            with self._directory(parts[:-1], create=True) as directory_fd:
                stage = "stage-" + uuid.uuid4().hex
                fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=self._internal_fd)
                try:
                    view = memoryview(data)
                    while view:
                        written = os.write(fd, view[:65_536])
                        view = view[written:]
                    os.fsync(fd)
                    os.close(fd)
                    fd = -1
                    if overwrite:
                        os.replace(stage, parts[-1], src_dir_fd=self._internal_fd, dst_dir_fd=directory_fd)
                    else:
                        os.link(stage, parts[-1], src_dir_fd=self._internal_fd, dst_dir_fd=directory_fd,
                                follow_symlinks=False)
                    os.fsync(directory_fd)
                finally:
                    if fd >= 0:
                        os.close(fd)
                    try:
                        os.unlink(stage, dir_fd=self._internal_fd)
                    except FileNotFoundError:
                        pass
            return self._artifact(path, data)

    def list_files(self, path: str = "", *, limit: int = 100) -> list[dict]:
        parts = self._parts(path, empty=True)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ToolError("invalid_arguments", "limit must be a positive integer at most 1000.")
        with self._locked():
            return self._inventory(parts)[:limit]

    def delete(self, path: str) -> dict:
        parts = self._parts(path)
        with self._locked(), self._directory(parts[:-1]) as directory_fd:
            info = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
            self._regular(info)
            os.unlink(parts[-1], dir_fd=directory_fd)
            os.fsync(directory_fd)
            return {"path": path, "deleted": True, "size_bytes": info.st_size}
