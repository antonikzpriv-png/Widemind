"""Small encrypted owner store, deliberately outside the writable workspace."""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import private_directory
from .tools.contracts import ToolError


class SecretStore:
    def __init__(self, directory: str | Path):
        self.root = private_directory(Path(directory))
        self._key = self.root / "secrets.key"
        try:
            fd = os.open(self._key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
                stream.flush()
                os.fsync(stream.fileno())
        self._check(self._key)
        self._cipher = Fernet(self._key.read_bytes())

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
        fd = os.open(self.root / "secrets.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            values = self._read()
            values[name] = value
            payload = self._cipher.encrypt(json.dumps(values).encode())
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
