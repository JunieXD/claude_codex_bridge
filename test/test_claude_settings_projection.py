import json

import pytest

from provider_backends.claude.launcher_runtime import home
from provider_backends.claude.launcher_runtime.settings_projection import merge_projected_permissions


pytestmark = pytest.mark.usefixtures('stub_claude_private_keychain')


def write_settings(root, payload):
    path = root / '.claude' / 'settings.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def hook(command):
    return {'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': command}]}


def materialize(source, target):
    layout = home.materialize_claude_home_config(target, source_home=source)
    return json.loads(layout.settings_path.read_text())


def test_new_global_deny_is_preserved_with_existing_runtime_permissions(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'permissions': {'allow': ['mcp__codegraph__*'], 'deny': ['Agent']}})
    write_settings(target, {'permissions': {'allow': ['Bash(ccb ask *)'], 'defaultMode': 'auto'}})
    merged = materialize(source, target)
    assert merged['permissions']['deny'] == ['Agent']
    assert merged['permissions']['allow'] == ['Bash(ccb ask *)']
    assert merged['permissions']['defaultMode'] == 'auto'


def test_hook_replacement_removal_and_tilde_paths_keep_local_hooks(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'hooks': {'PreToolUse': [hook('~/old-hook')]}})
    payload = materialize(source, target)
    inherited = payload['hooks']['PreToolUse']
    assert inherited == [hook(f'{source}/old-hook')]
    payload['hooks']['PreToolUse'].append(hook('echo local-hook'))
    write_settings(target, payload)
    write_settings(source, {'hooks': {'PreToolUse': [hook('~/new-hook')]}})
    payload = materialize(source, target)
    assert payload['hooks']['PreToolUse'] == [hook(f'{source}/new-hook'), hook('echo local-hook')]
    assert materialize(source, target)['hooks'] == payload['hooks']
    write_settings(source, {})
    assert materialize(source, target)['hooks'] == {'PreToolUse': [hook('echo local-hook')]}
    manifest = json.loads((target / '.claude' / '.ccb-settings-projection.json').read_text())
    assert manifest == {'schema_version': 1, 'settings': {}}
    assert json.loads((source / '.claude' / 'settings.json').read_text()) == {}


def test_source_permissions_refresh_but_agent_local_rules_survive(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'permissions': {'allow': ['Read'], 'deny': ['Agent'], 'defaultMode': 'auto'}})
    payload = materialize(source, target)
    payload['permissions']['deny'].append('Bash(rm *)')
    payload['permissions']['allow'].append('Bash(ccb ask *)')
    write_settings(target, payload)
    write_settings(source, {'permissions': {'allow': ['Write'], 'deny': ['WebFetch'], 'defaultMode': 'default'}})
    payload = materialize(source, target)
    assert payload['permissions'] == {
        'allow': ['Write', 'Bash(ccb ask *)'], 'deny': ['WebFetch', 'Bash(rm *)'], 'defaultMode': 'default',
    }


def test_explicit_local_scalar_override_survives_source_refresh():
    assert merge_projected_permissions(
        {'defaultMode': 'default', 'deny': ['Agent']},
        {'defaultMode': 'auto', 'deny': ['Agent', 'WebFetch']},
        {'defaultMode': 'bypassPermissions', 'deny': ['Agent']},
    ) == {'defaultMode': 'auto', 'deny': ['Agent', 'WebFetch']}


def test_role_enforcement_keeps_global_restrictions(monkeypatch):
    monkeypatch.setattr(home, 'role_command_policy_requires_enforcement', lambda policy: True)
    monkeypatch.setattr(home, 'claude_permission_allowlist', lambda policy: ['Bash(ccb ask *)'])
    payload = home._merge_settings_payload(
        {'permissions': {'deny': ['Agent'], 'ask': ['WebFetch']}},
        existing={'permissions': {'deny': ['Bash(rm *)']}},
    )
    assert payload['permissions'] == {
        'allow': ['Bash(ccb ask *)'], 'deny': ['Agent', 'Bash(rm *)'], 'ask': ['WebFetch'],
    }


def test_projection_manifest_never_copies_credentials(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'env': {'ANTHROPIC_AUTH_TOKEN': 'private-token'}, 'hooks': {'Stop': [hook('echo done')]}})
    materialize(source, target)
    manifest = (target / '.claude' / '.ccb-settings-projection.json').read_text()
    assert 'private-token' not in manifest
    assert 'ANTHROPIC_AUTH_TOKEN' not in manifest


def test_first_refresh_preserves_legacy_hooks_without_guessing_ownership(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'hooks': {'Stop': [hook('echo current')]}})
    write_settings(target, {'hooks': {'Stop': [hook('echo local-or-legacy')]}})
    assert materialize(source, target)['hooks']['Stop'] == [hook('echo current'), hook('echo local-or-legacy')]


def test_ccb_allowlist_does_not_hide_agent_local_ask_restrictions():
    payload = home._merge_settings_payload(
        {'permissions': {'allow': ['Read'], 'deny': ['Agent']}},
        existing={'permissions': {'allow': ['Bash(ccb ask *)'], 'ask': ['WebFetch']}},
        auto_permission=True,
    )
    assert payload['permissions']['deny'] == ['Agent']
    assert payload['permissions']['ask'] == ['WebFetch']
