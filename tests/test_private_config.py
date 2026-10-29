import json
import os

import pytest

from widemind.config import load_config
from widemind.secrets import SecretStore
from widemind.tools.contracts import ToolError


def write_config(path,config):
    path.write_text(json.dumps(config))
    path.chmod(0o600)


def test_config_rejects_overlap_and_workspace_config(tmp_path):
    config = {"control_dir":str(tmp_path/"control"),"workspace_dir":str(tmp_path/"workspace")}
    path = tmp_path/"owner.json"
    write_config(path,config)
    assert load_config(path)["policy"]["profile"] == "development"
    config["control_dir"] = str(tmp_path/"workspace"/"private")
    write_config(path,config)
    with pytest.raises(ToolError,match="overlapping"):
        load_config(path)
    config["control_dir"] = str(tmp_path/"other")
    config["workspace_dir"] = str(tmp_path)
    write_config(path,config)
    with pytest.raises(ToolError):
        load_config(path)


def test_config_rejects_symlinks_and_readable_owner_file(tmp_path):
    path = tmp_path/"owner.json"
    config = {"control_dir":str(tmp_path/"control"),"workspace_dir":str(tmp_path/"workspace")}
    write_config(path,config)
    path.chmod(0o644)
    with pytest.raises(ToolError,match="0600"):
        load_config(path)
    path.chmod(0o600)
    alias = tmp_path/"alias.json"
    alias.symlink_to(path)
    with pytest.raises(ToolError,match="symlink"):
        load_config(alias)


def test_secret_encryption_durable_update_private_permissions(tmp_path):
    store = SecretStore(tmp_path/"control")
    store.set("fixture_token","not-a-real-token-abc")
    store.set("another","value")
    assert SecretStore(store.root).get("fixture_token") == "not-a-real-token-abc"
    assert store.get("missing") is None
    assert b"not-a-real-token" not in (store.root/"secrets.enc").read_bytes()
    assert (store.root/"secrets.key").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ToolError):
        store.get("../arbitrary")
