import json
import os
from pathlib import Path
import subprocess
from io import StringIO
from types import SimpleNamespace

import pytest

from cli.management_runtime import fork_runtime, source_update
from cli.management_runtime.commands_runtime import install as install_commands
from cli.management_runtime.commands_runtime import update as update_commands
from cli.management_runtime.commands_runtime import version as version_commands
from cli.management_runtime.startup_update_state import startup_release_update_supported
from cli import entrypoint_runtime


def write_runtime(root, *, source=None, commit=None):
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        'schema_version': 1, 'repository': source_update.FORK_REPOSITORY, 'branch': 'main',
        'commit': commit or 'a' * 40, 'installation_root': str(root),
        'source_root': str(source or root.parent / 'missing-development-disk'),
        'bin_dir': str(root.parent / 'bin'),
    }
    (root / fork_runtime.MANIFEST_NAME).write_text(json.dumps(payload))
    (root / 'BUILD_INFO.json').write_text(json.dumps({
        'source_kind': fork_runtime.SOURCE_KIND, 'install_mode': 'release', 'channel': 'fork',
    }))
    return payload


@pytest.mark.parametrize('field,value', [
    ('schema_version', 2), ('repository', 'SeemSeam/claude_codex_bridge'),
    ('installation_root', '/another-runtime'), ('source_root', 'relative-path'),
    ('commit', 'invalid'), ('branch', '--other'), ('branch', '../main'),
])
def test_invalid_runtime_metadata_is_rejected(tmp_path, field, value):
    root = tmp_path / 'runtime'
    payload = write_runtime(root)
    payload[field] = value
    (root / fork_runtime.MANIFEST_NAME).write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        fork_runtime.load_fork_runtime(root)


def test_missing_disk_refuses_update_without_official_fallback(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'runtime'
    write_runtime(root)
    before = (root / fork_runtime.MANIFEST_NAME).read_bytes()
    monkeypatch.setattr(update_commands, '_resolve_target_version', lambda args: pytest.fail('Official updater was reached'))
    assert update_commands.cmd_update(SimpleNamespace(target=None), script_root=root) == 1
    assert (root / fork_runtime.MANIFEST_NAME).read_bytes() == before
    assert 'Connect the external disk' in capsys.readouterr().out


def test_missing_manifest_still_cannot_fall_back_to_official_updates(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    write_runtime(root)
    (root / fork_runtime.MANIFEST_NAME).unlink()
    monkeypatch.setattr(update_commands, '_resolve_target_version', lambda args: pytest.fail('Official updater was reached'))
    assert update_commands.cmd_update(SimpleNamespace(target=None), script_root=root) == 1


def test_runtime_status_does_not_need_the_development_checkout(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    write_runtime(root)
    commands = []

    def run(command, **keywords):
        commands.append(command)
        return SimpleNamespace(stdout='a' * 40 + '\trefs/heads/main\n')

    monkeypatch.setattr(fork_runtime.subprocess, 'run', run)
    assert fork_runtime.fork_runtime_update_status(root)['state'] == 'current'
    assert commands == [['git', 'ls-remote', '--refs', 'https://github.com/JunieXD/claude_codex_bridge.git', 'refs/heads/main']]


@pytest.mark.skipif(os.name == 'nt', reason='POSIX install locks')
def test_install_lock_blocks_startup_and_competing_installs(tmp_path):
    root = tmp_path / 'runtime'
    write_runtime(root)
    with fork_runtime.fork_runtime_lock(root, exclusive=True):
        with pytest.raises(ValueError, match='being updated'):
            with fork_runtime.fork_runtime_start_guard(root):
                pytest.fail('Startup was not blocked')
        with pytest.raises(ValueError):
            with fork_runtime.fork_runtime_lock(root, exclusive=True):
                pytest.fail('Competing installation was not blocked')
    with fork_runtime.fork_runtime_start_guard(root):
        with pytest.raises(ValueError):
            with fork_runtime.fork_runtime_lock(root, exclusive=True):
                pytest.fail('A startup can race with an install')


def test_explicit_rich_launch_cannot_bypass_install_lock(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    write_runtime(root)
    monkeypatch.setattr(entrypoint_runtime, 'cmd_rich', lambda **kwargs: pytest.fail('Workbench launched during an install'))
    output = StringIO()
    with fork_runtime.fork_runtime_lock(root, exclusive=True):
        code = entrypoint_runtime._dispatch_rich(['rich'], script_root=root, cwd=tmp_path,
                                                  stdout=output, stderr=output)
    assert code == 1
    assert 'being updated' in output.getvalue()


def test_fork_runtime_never_uses_upstream_startup_update_check():
    assert not startup_release_update_supported({
        'install_mode': 'release', 'source_kind': fork_runtime.SOURCE_KIND, 'channel': 'fork',
    }, platform_name='Darwin')


def test_version_command_routes_to_fork_runtime(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'runtime'
    write_runtime(root)
    monkeypatch.setattr(version_commands, 'fork_runtime_update_status', lambda root: {'state': 'current'})
    monkeypatch.setattr(version_commands, 'get_available_versions', lambda: pytest.fail('Upstream tags were fetched'))
    assert version_commands.cmd_version(SimpleNamespace(), script_root=root) == 0
    assert 'JunieXD/claude_codex_bridge' in capsys.readouterr().out


def test_reinstall_does_not_clear_provider_files(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    write_runtime(root)
    monkeypatch.setattr(install_commands, 'cleanup_claude_files', lambda: pytest.fail('Provider config was cleared'))
    calls = []
    monkeypatch.setattr(install_commands, 'update_fork_runtime', lambda args, **kwargs: calls.append(kwargs) or 0)
    assert install_commands.cmd_reinstall(SimpleNamespace(target=None), script_root=root) == 0
    assert calls == [{'script_root': root, 'rebuild': True}]


def test_uninstall_cannot_delete_provider_files_or_unrelated_install(tmp_path, monkeypatch):
    root = tmp_path / 'runtime'
    write_runtime(root)
    monkeypatch.setattr(install_commands, 'cleanup_claude_files', lambda: pytest.fail('Provider config was cleared'))
    assert install_commands.cmd_uninstall(SimpleNamespace(target=None), script_root=root) == 1
    assert root.exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX install locks')
def test_updater_passes_real_lock_descriptor_to_builder(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    (source / '.git').mkdir(parents=True)
    root = tmp_path / 'runtime'
    write_runtime(root, source=source)
    monkeypatch.setattr(fork_runtime, '_source_branch', lambda root: 'main')
    monkeypatch.setattr(fork_runtime, '_git', lambda *arguments: 'b' * 40)
    monkeypatch.setattr(fork_runtime, 'require_runtime_stopped', lambda root: None)
    monkeypatch.setattr(source_update, 'update_source_from_fork', lambda *arguments, **keywords: 0)
    calls = []

    def run(command, **keywords):
        descriptor = int(keywords['env']['CCB_FORK_INSTALL_LOCK_FD'])
        assert keywords['pass_fds'] == (descriptor,)
        assert os.fstat(descriptor).st_ino == fork_runtime.runtime_lock_path(root).stat().st_ino
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(fork_runtime.subprocess, 'run', run)
    assert fork_runtime.update_fork_runtime(SimpleNamespace(target=None), script_root=root) == 0
    assert calls[0][1] == str(source / 'scripts/install_fork_runtime.py')
    assert '--lock-held' in calls[0]


def test_runtime_update_does_not_rebuild_current_commit(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    (source / '.git').mkdir(parents=True)
    root = tmp_path / 'runtime'
    write_runtime(root, source=source)
    monkeypatch.setattr(fork_runtime, '_source_branch', lambda root: 'main')
    monkeypatch.setattr(fork_runtime, '_git', lambda *arguments: 'a' * 40)
    monkeypatch.setattr(fork_runtime, 'require_runtime_stopped', lambda root: None)
    monkeypatch.setattr(source_update, 'update_source_from_fork', lambda *arguments, **keywords: 0)
    monkeypatch.setattr(fork_runtime.subprocess, 'run', lambda *arguments, **keywords: pytest.fail('Current runtime was rebuilt'))
    assert fork_runtime.update_fork_runtime(SimpleNamespace(target=None), script_root=root) == 0
