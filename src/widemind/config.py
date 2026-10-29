"""Owner configuration is authority; tool inputs are never configuration."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from .tools.contracts import ToolError


def private_directory(path: Path) -> Path:
    path = path.absolute()
    # Reject existing symlink components, including the final component.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ToolError("configuration", "Private paths must not contain symlinks.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ToolError("configuration", "Private directories must be owned by this user with mode 0700.")
    return path.resolve()


def load_config(filename: str | Path) -> dict[str, Any]:
    path = Path(filename).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ToolError("configuration", "Owner config must not be a symlink.")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ToolError("configuration", "Owner config must be a private regular file (0600).")
    if info.st_size > 1_000_000:
        raise ToolError("configuration", "Owner config exceeds its size limit.")
    try:
        config = json.loads(path.read_text())
        if not isinstance(config, dict):
            raise ValueError()
        control = Path(config["control_dir"])
        workspace = Path(config["workspace_dir"])
        if not control.is_absolute() or not workspace.is_absolute():
            raise ValueError()
        for location in (control, workspace):
            if any(p.is_symlink() for p in (location, *location.parents)):
                raise ValueError()
        control, workspace = control.resolve(), workspace.resolve()
        if control == workspace or control in workspace.parents or workspace in control.parents:
            raise ValueError()
        if workspace == path or workspace in path.parents or control == path or control in path.parents:
            # Keep operator config outside both managed trees.
            raise ValueError()
        policy = config.setdefault("policy", {})
        tools = config.setdefault("tools", {})
        if not isinstance(policy, dict) or not isinstance(tools, dict):
            raise ValueError()
        if policy.get("profile", "development") not in {"development", "sandbox", "production"}:
            raise ValueError()
        config["control_dir"], config["workspace_dir"] = str(control), str(workspace)
        config["policy"].setdefault("profile", "development")
        return config
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("configuration", "Invalid owner configuration or overlapping private/workspace paths.") from exc
