"""Small encrypted owner store, deliberately outside the writable workspace."""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import private_directory
from .tools.contracts import ToolError


class SecretStore:
    def __init__(self, directory: str | Path):
        self.root = private_directory(Path(directory))
        self._key = self.root / "secrets.key"
        with self._lock():
            if not self._key.exists():
                fd, tmp = tempfile.mkstemp(dir=self.root,prefix=".key-")
                try:
                    with os.fdopen(fd,"wb") as stream:
                        stream.write(Fernet.generate_key())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(tmp,self._key,follow_symlinks=False)
                    directory = os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    os.unlink(tmp)
            self._check(self._key)
            if self._key.stat().st_size != 44:
                raise ToolError("configuration", "Secret encryption key is malformed; restore the private owner backup.")
            self._cipher = Fernet(self._key.read_bytes())

    @contextmanager
    def _lock(self):
        fd = os.open(self.root / "secrets.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd,"r+b") as lock:
            end = time.monotonic()+5
            while True:
                try:
                    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= end:
                        raise ToolError("busy", "Secret store is busy; retry this administrative operation later.") from None
                    time.sleep(.02)
            yield

    @staticmethod
    def _check(path: Path) -> None:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ToolError("configuration", "Secret files must be private, owner-owned regular files.")

    @staticmethod
    def _name(name: str) -> None:
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name):
            raise ToolError("invalid_input", "Invalid secret reference name.")

    def _read(self) -> dict:
        path = self.root / "secrets.enc"
        if not path.exists():
            return {}
        self._check(path)
        if path.stat().st_size > 16_000_000:
            raise ToolError("configuration", "Secret store exceeds its limit.")
        try:
            return json.loads(self._cipher.decrypt(path.read_bytes()))
        except (InvalidToken, ValueError) as exc:
            raise ToolError("configuration", "Secret store cannot be decrypted.") from exc

    def get(self, name: str) -> str | None:
        self._name(name)
        return self._read().get(name)

    def set(self, name: str, value: str) -> None:
        self._name(name)
        if not isinstance(value, str) or len(value.encode()) > 2_000_000:
            raise ToolError("invalid_input", "Secret must be a bounded string.")
        with self._lock():
            values = self._read()
            values[name] = value
            payload = self._cipher.encrypt(json.dumps(values).encode())
            if len(payload) > 16_000_000:
                raise ToolError("output_limit", "Secret update would exceed the encrypted store limit; existing values retained.")
            fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".secret-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self.root / "secrets.enc")
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
