import json
import os
from concurrent.futures import ThreadPoolExecutor

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


def test_concurrent_secret_initialization_and_updates_do_not_lose_key_or_values(tmp_path):
    root = tmp_path/"private"
    def create(i):
        store = SecretStore(root)
        store.set("fixture-"+str(i),str(i))
        return store
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(create,range(24)))
    store = SecretStore(root)
    assert [store.get("fixture-"+str(i)) for i in range(24)] == [str(i) for i in range(24)]


def test_secret_size_failure_preserves_existing_store(tmp_path):
    store = SecretStore(tmp_path/"private")
    for i in range(5):
        store.set("large-"+str(i),"x"*2_000_000)
    before = (store.root/"secrets.enc").read_bytes()
    with pytest.raises(ToolError,match="exceed"):
        store.set("too-large","y"*2_000_000)
    assert (store.root/"secrets.enc").read_bytes() == before
    assert store.get("large-0") == "x"*2_000_000
    assert store.get("too-large") is None
