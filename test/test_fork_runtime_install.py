import json
import os
from pathlib import Path
import subprocess

import pytest

from cli.management_runtime import fork_runtime_install as installer
from cli.management_runtime.fork_runtime import MANIFEST_NAME, load_fork_runtime


def git(root, *arguments):
    return subprocess.run(['git', '-C', str(root), *arguments], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / 'development'
    root.mkdir()
    git(root, 'init', '--initial-branch=main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.test')
    git(root, 'remote', 'add', 'origin', 'https://github.com/JunieXD/claude_codex_bridge.git')
    for relative in (*installer.ENTRYPOINTS, 'install.sh'):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
    (root / 'package.json').write_text(json.dumps({'version': '8.7.5'}))
    (root / 'lib').mkdir()
    (root / 'lib/runtime.py').write_text('runtime = True\n')
    (root / 'test').mkdir()
    (root / 'test/private-fixture.py').write_text('test_only = True\n')
    (root / '.gitignore').write_text('target/\n.venv/\n')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'fixture')
    (root / 'target').mkdir()
    (root / 'target/build-cache').write_text('cache')
    return root


@pytest.fixture
def offline_build(monkeypatch):
    monkeypatch.setattr(installer, '_build_native_helpers', lambda *arguments: None)
    monkeypatch.setattr(installer, '_copy_python_environment', lambda *arguments: None)
    monkeypatch.setattr(installer, '_verify_runtime', lambda root: None)


def test_export_contains_only_tracked_runtime_assets(checkout, tmp_path):
    destination = tmp_path / 'package'
    destination.mkdir()
    installer._export_runtime(checkout, destination, git(checkout, 'rev-parse', 'HEAD'))
    assert (destination / 'lib/runtime.py').is_file()
    for name in ('.git', 'target', '.venv', 'test'):
        assert not (destination / name).exists()


def test_packaged_build_helpers_rebuild_the_separated_runtime(tmp_path):
    stage = tmp_path / 'stage'
    (stage / 'bin').mkdir(parents=True)
    root = tmp_path / 'installed runtime'
    root.mkdir()
    receipt = root / 'arguments.txt'
    launcher = root / 'ccb'
    launcher.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/arguments.txt"\n')
    launcher.chmod(0o755)
    installer._package_build_entrypoints(stage, root)
    for _, name in installer.NATIVE_HELPERS:
        subprocess.run([str(stage / 'bin' / f'build-{name}')], check=True)
        assert receipt.read_text() == 'reinstall\n'


def test_install_creates_independent_stable_links(checkout, tmp_path, offline_build):
    root = tmp_path / 'runtime'
    bin_dir = tmp_path / 'commands'
    installer.install_fork_runtime(checkout, root, bin_dir)
    metadata = load_fork_runtime(root)
    assert metadata['source_root'] == str(checkout)
    assert metadata['commit'] == git(checkout, 'rev-parse', 'HEAD')
    assert (bin_dir / 'ccb').resolve() == root / 'ccb'
    assert not (root / '.git').exists()
    checkout.rename(checkout.with_name('detached-development'))
    assert (bin_dir / 'ccb').exists()
    assert subprocess.run([str(bin_dir / 'ccb')], check=False).returncode == 0


def test_reinstall_replaces_runtime_and_keeps_one_rollback(checkout, tmp_path, offline_build):
    root = tmp_path / 'runtime'
    bin_dir = tmp_path / 'commands'
    installer.install_fork_runtime(checkout, root, bin_dir)
    old_metadata = (root / MANIFEST_NAME).read_bytes()
    (checkout / 'lib/runtime.py').write_text('runtime = "new"\n')
    git(checkout, 'add', '.')
    git(checkout, 'commit', '-m', 'new')
    installer.install_fork_runtime(checkout, root, bin_dir)
    assert (root / 'lib/runtime.py').read_text() == 'runtime = "new"\n'
    assert (tmp_path / '.runtime.previous' / MANIFEST_NAME).read_bytes() == old_metadata
    assert (bin_dir / 'ccb').resolve() == root / 'ccb'


@pytest.mark.parametrize('failure_stage', ['build', 'verify', 'changed_source', 'link'])
def test_install_failure_keeps_previous_runtime_and_commands(checkout, tmp_path, offline_build, monkeypatch, failure_stage):
    root = tmp_path / 'runtime'
    bin_dir = tmp_path / 'commands'
    installer.install_fork_runtime(checkout, root, bin_dir)
    before = (root / MANIFEST_NAME).read_bytes()
    target = os.readlink(bin_dir / 'ccb')

    def fail(*arguments):
        raise ValueError('fixture failure')

    if failure_stage == 'build':
        monkeypatch.setattr(installer, '_build_native_helpers', fail)
    elif failure_stage == 'verify':
        monkeypatch.setattr(installer, '_verify_runtime', fail)
    elif failure_stage == 'changed_source':
        monkeypatch.setattr(installer, '_verify_runtime', lambda root: (checkout / 'unreviewed.txt').write_text('changed'))
    else:
        monkeypatch.setattr(installer, '_configure_entrypoints', fail)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        installer.install_fork_runtime(checkout, root, bin_dir)
    assert (root / MANIFEST_NAME).read_bytes() == before
    assert os.readlink(bin_dir / 'ccb') == target
    assert not list(tmp_path.glob('.runtime.staging-*'))


def test_partial_link_failure_restores_old_links(checkout, tmp_path, offline_build, monkeypatch):
    root = tmp_path / 'runtime'
    bin_dir = tmp_path / 'commands'
    bin_dir.mkdir()
    for relative in installer.ENTRYPOINTS:
        target = checkout / relative
        (bin_dir / target.name).symlink_to(target)
    original = installer._replace_link
    calls = []

    def replace(entry, target):
        calls.append(entry)
        if len(calls) == 4:
            raise OSError('fixture link failure')
        original(entry, target)

    monkeypatch.setattr(installer, '_replace_link', replace)
    with pytest.raises(OSError):
        installer.install_fork_runtime(checkout, root, bin_dir)
    assert not root.exists()
    for relative in installer.ENTRYPOINTS:
        assert (bin_dir / Path(relative).name).resolve() == checkout / relative


@pytest.mark.parametrize('occupied', ['runtime', 'executable', 'rollback'])
def test_installer_does_not_overwrite_unowned_paths(checkout, tmp_path, offline_build, occupied):
    root = tmp_path / 'runtime'
    bin_dir = tmp_path / 'commands'
    path = root if occupied == 'runtime' else tmp_path / '.runtime.previous' if occupied == 'rollback' else bin_dir
    path.mkdir()
    marker = path / ('ccb' if occupied == 'executable' else 'private.txt')
    marker.write_text('user owned')
    with pytest.raises(ValueError):
        installer.install_fork_runtime(checkout, root, bin_dir)
    assert marker.read_text() == 'user owned'


def test_runtime_cannot_be_inside_the_development_checkout(checkout, tmp_path, offline_build):
    with pytest.raises(ValueError, match='separate directories'):
        installer.install_fork_runtime(checkout, checkout / 'runtime', tmp_path / 'commands')
