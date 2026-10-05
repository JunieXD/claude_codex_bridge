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


@pytest.mark.parametrize('fresh_hooks', [[hook('echo new')], []])
def test_local_hook_in_inherited_group_does_not_keep_removed_command(tmp_path, fresh_hooks):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'hooks': {'PreToolUse': [hook('echo old')]}})
    payload = materialize(source, target)
    payload['hooks']['PreToolUse'][0]['hooks'].append({'type': 'command', 'command': 'echo local'})
    write_settings(target, payload)
    write_settings(source, {'hooks': {'PreToolUse': fresh_hooks}})
    payload = materialize(source, target)
    assert payload['hooks']['PreToolUse'] == [*fresh_hooks, hook('echo local')]
    assert materialize(source, target)['hooks'] == payload['hooks']


def test_local_hook_promoted_to_source_is_not_duplicated(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'hooks': {'PreToolUse': [hook('echo old')]}})
    payload = materialize(source, target)
    local = {'type': 'command', 'command': 'echo local'}
    payload['hooks']['PreToolUse'][0]['hooks'].append(local)
    write_settings(target, payload)
    fresh = hook('echo new')
    fresh['hooks'].append(local)
    write_settings(source, {'hooks': {'PreToolUse': [fresh]}})
    assert materialize(source, target)['hooks']['PreToolUse'] == [fresh]


def test_locally_changed_hook_matcher_is_preserved(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'hooks': {'PreToolUse': [hook('echo original')]}})
    payload = materialize(source, target)
    payload['hooks']['PreToolUse'][0]['matcher'] = 'Read'
    write_settings(target, payload)
    write_settings(source, {})
    assert materialize(source, target)['hooks']['PreToolUse'][0]['matcher'] == 'Read'


def test_removed_inherited_plugin_is_removed_but_local_plugin_survives(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'enabledPlugins': {'old@marketplace': True}})
    payload = materialize(source, target)
    payload['enabledPlugins']['local@marketplace'] = True
    write_settings(target, payload)
    write_settings(source, {'enabledPlugins': {'new@marketplace': True}})
    payload = materialize(source, target)
    assert payload['enabledPlugins'] == {'new@marketplace': True, 'local@marketplace': True}
    assert materialize(source, target)['enabledPlugins'] == payload['enabledPlugins']
    write_settings(source, {})
    assert materialize(source, target)['enabledPlugins'] == {'local@marketplace': True}


def test_removed_inherited_plugin_retains_explicit_local_disable(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'enabledPlugins': {'plugin@marketplace': True}})
    payload = materialize(source, target)
    payload['enabledPlugins']['plugin@marketplace'] = False
    write_settings(target, payload)
    write_settings(source, {})
    assert materialize(source, target)['enabledPlugins'] == {'plugin@marketplace': False}


def test_removing_last_inherited_plugin_removes_enabled_plugins_key(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {'enabledPlugins': {'plugin@marketplace': True}})
    materialize(source, target)
    write_settings(source, {})
    assert 'enabledPlugins' not in materialize(source, target)


def test_legacy_plugin_enablement_without_baseline_is_preserved(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'agent'
    write_settings(source, {})
    write_settings(target, {'enabledPlugins': {'unknown@marketplace': True}})
    assert materialize(source, target)['enabledPlugins'] == {'unknown@marketplace': True}
