"""The committed environment template must launch accounting without optional tools."""
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from widemind.economy.app import create_app
from widemind.economy.config import Settings


@pytest.fixture
def role_environment(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith('WIDEMIND_'):
            monkeypatch.delenv(name)
    required = {
        'WIDEMIND_OPERATOR_TOKEN': 'fixture-operator-' + 'o' * 48,
        'WIDEMIND_GATEWAY_TOKEN': 'fixture-gateway-' + 'g' * 48,
        'WIDEMIND_AGENT_TOKEN': 'fixture-agent-' + 'a' * 48,
        'WIDEMIND_AGENT_RUN_ID': 'fixture-run',
        'WIDEMIND_ECONOMY_DB': str(tmp_path / 'accounting.sqlite3'),
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    return required


def test_shared_template_optional_defaults_start_accounting_only(monkeypatch, role_environment):
    template = Path(__file__).resolve().parents[1] / '.env.example'
    for line in template.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        name, value = line.split('=', 1)
        monkeypatch.setenv(name, value)
    for name, value in role_environment.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.runtime_tool_profiles == {}
    assert settings.runtime_auth_secret is None
    assert settings.stripe_enabled is False
    with TestClient(create_app(settings)) as app:
        assert app.get('/health').status_code == 200
        assert app.app.state.runtime_bridge is None


@pytest.mark.parametrize('profiles', [None, '', '   ', '\t\r\n'])
def test_absent_or_blank_optional_profiles_are_an_empty_map(monkeypatch, role_environment, profiles):
    if profiles is not None:
        monkeypatch.setenv('WIDEMIND_RUNTIME_TOOL_PROFILES', profiles)
    assert Settings.from_env().runtime_tool_profiles == {}


@pytest.mark.parametrize('profiles', ['', ' \t ', '{}'])
def test_persistent_key_and_empty_profiles_preserve_reconciliation_only_mode(monkeypatch, role_environment, profiles):
    monkeypatch.setenv('WIDEMIND_RUNTIME_AUTH_SECRET', 'fixture-signing-' + 's' * 48)
    monkeypatch.setenv('WIDEMIND_RUNTIME_TOOL_PROFILES', profiles)
    settings = Settings.from_env()
    assert settings.runtime_tool_profiles == {}
    with TestClient(create_app(settings)) as app:
        assert app.app.state.runtime_bridge is not None
        assert app.app.state.runtime_bridge.profiles == {}


@pytest.mark.parametrize('profiles', ['{', 'not-json', 'null', '[]', '0'])
def test_nonempty_invalid_profiles_fail_closed(monkeypatch, role_environment, profiles):
    monkeypatch.setenv('WIDEMIND_RUNTIME_TOOL_PROFILES', profiles)
    with pytest.raises(ValueError):
        Settings.from_env()


def test_nonempty_profiles_still_require_a_signing_key(monkeypatch, role_environment):
    monkeypatch.setenv('WIDEMIND_RUNTIME_TOOL_PROFILES',
                       '{"fixture.tool":{"provider":"fixture","account":"sandbox","mode":"sandbox"}}')
    with pytest.raises(ValueError, match='persistent authorization secret'):
        Settings.from_env()


def test_oversized_blank_configuration_does_not_bypass_the_input_limit(monkeypatch, role_environment):
    monkeypatch.setenv('WIDEMIND_RUNTIME_TOOL_PROFILES', ' ' * 65537)
    with pytest.raises(ValueError, match='exceeds 64 KiB'):
        Settings.from_env()
