import subprocess
from types import SimpleNamespace

import pytest

from cli.management_runtime import source_update


def git(root, *arguments):
    return subprocess.run(['git', '-C', str(root), *arguments], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def fork_checkout(tmp_path, monkeypatch):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(remote)],
                   check=True, capture_output=True)
    publisher = tmp_path / 'publisher'
    subprocess.run(['git', 'clone', str(remote), str(publisher)], check=True, capture_output=True)
    git(publisher, 'config', 'user.name', 'Test')
    git(publisher, 'config', 'user.email', 'test@example.test')
    (publisher / 'install.sh').write_text('initial\n')
    git(publisher, 'add', 'install.sh')
    git(publisher, 'commit', '-m', 'initial')
    git(publisher, 'push', 'origin', 'main')
    checkout = tmp_path / 'checkout'
    subprocess.run(['git', 'clone', str(remote), str(checkout)], check=True, capture_output=True)
    git(checkout, 'config', 'user.name', 'Test')
    git(checkout, 'config', 'user.email', 'test@example.test')
    monkeypatch.setattr(source_update, '_is_fork_remote', lambda remote_url: True)
    return checkout, publisher


def publish(publisher):
    (publisher / 'install.sh').write_text('updated\n')
    git(publisher, 'add', 'install.sh')
    git(publisher, 'commit', '-m', 'update')
    git(publisher, 'push', 'origin', 'main')


def test_fork_source_update_fast_forwards_without_installing(fork_checkout, capsys):
    checkout, publisher = fork_checkout
    publish(publisher)
    assert source_update.update_source_from_fork(SimpleNamespace(target=None), script_root=checkout) == 0
    assert git(checkout, 'rev-parse', 'HEAD') == git(publisher, 'rev-parse', 'HEAD')
    assert 'not restarted' in capsys.readouterr().out


def test_fork_source_update_up_to_date(fork_checkout, capsys):
    checkout, _publisher = fork_checkout
    assert source_update.update_source_from_fork(SimpleNamespace(target=None), script_root=checkout) == 0
    assert 'already up to date' in capsys.readouterr().out


@pytest.mark.parametrize('state', ['tracked', 'untracked', 'detached', 'diverged', 'operation'])
def test_fork_source_update_refuses_unsafe_states(fork_checkout, state):
    checkout, publisher = fork_checkout
    publish(publisher)
    if state == 'tracked':
        (checkout / 'install.sh').write_text('local draft\n')
    elif state == 'untracked':
        (checkout / 'local.txt').write_text('local draft\n')
    elif state == 'detached':
        git(checkout, 'checkout', '--detach')
    elif state == 'operation':
        (checkout / '.git' / 'CHERRY_PICK_HEAD').write_text(git(checkout, 'rev-parse', 'HEAD'))
    else:
        (checkout / 'local.txt').write_text('local change\n')
        git(checkout, 'add', 'local.txt')
        git(checkout, 'commit', '-m', 'local')
    before = git(checkout, 'rev-parse', 'HEAD')
    assert source_update.update_source_from_fork(SimpleNamespace(target=None), script_root=checkout) == 1
    assert git(checkout, 'rev-parse', 'HEAD') == before


def test_fork_source_update_rejects_release_target(fork_checkout):
    checkout, _publisher = fork_checkout
    assert source_update.update_source_from_fork(SimpleNamespace(target='8.7.5'), script_root=checkout) == 1


def test_fork_source_update_rejects_wrong_origin(fork_checkout, monkeypatch):
    checkout, _publisher = fork_checkout
    monkeypatch.setattr(source_update, '_is_fork_remote', lambda remote_url: False)
    assert source_update.update_source_from_fork(SimpleNamespace(target=None), script_root=checkout) == 1


@pytest.mark.parametrize('remote_url,expected', [
    ('https://github.com/JunieXD/claude_codex_bridge.git', True),
    ('git@github.com:JunieXD/claude_codex_bridge.git', True),
    ('ssh://git@github.com/JunieXD/claude_codex_bridge.git', True),
    ('https://github.com/SeemSeam/claude_codex_bridge.git', False),
    ('https://github.com.evil.test/JunieXD/claude_codex_bridge.git', False),
])
def test_fork_remote_validation(remote_url, expected):
    assert source_update._is_fork_remote(remote_url) is expected
