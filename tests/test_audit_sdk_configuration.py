"""Explicit invalid owner authority must fail before any SDK state is created."""
import json

import pytest

from widemind.gateway import Gateway
from widemind.tools.contracts import ToolError


@pytest.mark.parametrize('authority', ['CENTRAL', 'central ', '', None, True, 1, ['central'], {'mode': 'central'}])
@pytest.mark.parametrize('entrypoint', ['direct', 'private-file'])
def test_invalid_explicit_sdk_authority_is_not_a_standalone_fallback(tmp_path, authority, entrypoint):
    control = tmp_path / 'control'
    workspace = tmp_path / 'workspace'
    config = {'control_dir': str(control), 'workspace_dir': str(workspace),
              'policy': {'profile': 'development', 'accounting_authority': authority}, 'tools': {}}
    owner = tmp_path / 'owner.json'
    owner.write_text(json.dumps(config))
    owner.chmod(0o600)
    with pytest.raises(ToolError) as failure:
        if entrypoint == 'direct':
            Gateway(config, adapters=[], transport=object())
        else:
            Gateway.from_config(owner, adapters=[], transport=object())
    assert failure.value.code == 'configuration'
    assert not control.exists()
    assert not workspace.exists()


@pytest.mark.parametrize('authority', ['central', 'standalone', None])
def test_valid_or_absent_sdk_authority_preserves_owner_setup(tmp_path, authority):
    policy = {'profile': 'development'}
    if authority is not None:
        policy['accounting_authority'] = authority
    gateway = Gateway({'control_dir': str(tmp_path / 'control'), 'workspace_dir': str(tmp_path / 'workspace'),
                       'policy': policy, 'tools': {}}, adapters=[], transport=object())
    assert gateway.status()['tools'] == {}
    assert gateway.store.path.is_file()
