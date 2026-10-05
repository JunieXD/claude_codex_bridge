import subprocess
import os
import sys
from types import SimpleNamespace

import pytest

from cli.management_runtime import source_update


def git(root, *arguments):
    return subprocess.run(['git', '-C', str(root), *arguments], check=True,
                          text=True, capture_output=True).stdout.strip()


@pytest.fixture
def fork_checkout(tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('Source updating is supported on Linux/macOS/WSL')
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


@pytest.mark.parametrize('state', ['current', 'ahead', 'behind', 'diverged'])
def test_source_status_compares_origin_ancestry(fork_checkout, state):
    checkout, publisher = fork_checkout
    before = git(checkout, 'rev-parse', 'HEAD')
    if state in {'behind', 'diverged'}:
        publish(publisher)
    if state in {'ahead', 'diverged'}:
        (checkout / 'local.txt').write_text('local commit')
        git(checkout, 'add', 'local.txt')
        git(checkout, 'commit', '-m', 'local')
        before = git(checkout, 'rev-parse', 'HEAD')
    status = source_update.source_update_status(checkout)
    assert status['state'] == state
    assert status['ahead'] == int(state in {'ahead', 'diverged'})
    assert status['behind'] == int(state in {'behind', 'diverged'})
    assert git(checkout, 'rev-parse', 'HEAD') == before


@pytest.mark.parametrize('during_fetch', [False, True])
def test_update_refuses_live_source_runtime_before_merge(fork_checkout, monkeypatch, capsys, during_fetch):
    checkout, publisher = fork_checkout
    publish(publisher)
    before = git(checkout, 'rev-parse', 'HEAD')
    checks = []
    def guard(root):
        checks.append(root)
        if not during_fetch or len(checks) == 2:
            raise ValueError('Source runtimes are still running')
    monkeypatch.setattr(source_update, '_require_source_runtimes_stopped', guard)
    assert source_update.update_source_from_fork(SimpleNamespace(target=None), script_root=checkout) == 1
    assert git(checkout, 'rev-parse', 'HEAD') == before
    assert 'still running' in capsys.readouterr().out


def test_runtime_detection_covers_spaces_and_ignores_other_installs(tmp_path, monkeypatch):
    root = tmp_path / 'source with spaces'
    monkeypatch.setattr(source_update.os, 'getpid', lambda: 100)
    table = '\n'.join([
        f'100 python "{root}/ccb.py" update',
        f'101 python {root}/lib/ccbd/main.py --project /tmp/project',
        f'102 python "{root}/lib/ccbd/keeper_main.py" --project /tmp/project',
        f'103 {root}/tools/ccb-agent-sidebar/target/release/ccb-agent-sidebar',
        '104 python /another-install/lib/ccbd/main.py --project /tmp/project',
        f'105 python "{root}/ccb.py.old"',
        f'106 sh -c "{root}/ccb update"',
    ])
    assert source_update._source_runtime_processes(root, table) == [101, 102, 103]


def test_live_process_refuses_update_without_touching_checkout(fork_checkout, capsys):
    checkout, publisher = fork_checkout
    script = publisher / 'lib' / 'ccbd' / 'main.py'
    script.parent.mkdir(parents=True)
    script.write_text('import time\ntime.sleep(60)\n')
    git(publisher, 'add', 'lib/ccbd/main.py')
    git(publisher, 'commit', '-m', 'runtime fixture')
    git(publisher, 'push', 'origin', 'main')
    git(checkout, 'pull', '--ff-only')
    before = git(checkout, 'rev-parse', 'HEAD')
    publish(publisher)
    process = subprocess.Popen([sys.executable, str(checkout / 'lib' / 'ccbd' / 'main.py')])
    try:
        assert source_update.update_source_from_fork(SimpleNamespace(target=None), script_root=checkout) == 1
        assert git(checkout, 'rev-parse', 'HEAD') == before
        assert 'still running' in capsys.readouterr().out
    finally:
        process.terminate()
        process.wait(timeout=5)
