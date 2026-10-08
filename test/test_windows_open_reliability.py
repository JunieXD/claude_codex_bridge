from io import StringIO
from types import SimpleNamespace
import subprocess
import sys
from pathlib import Path

import pytest

from cli.models import ParsedHerdrOpenCommand
from platforms.windows.herdr import entrypoint as handlers
from platforms.windows.herdr.runtime.cli import HerdrCliRequestAdapter
from terminal_runtime.mux_backend_contract import MuxCommandErrorV2


def setup_open(monkeypatch, tmp_path, *, running=True, ready=True):
    context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path), paths=None)
    monkeypatch.setattr('ccbd.services.project_namespace_state_runtime.stores.ProjectNamespaceStateStore',
                        lambda paths: SimpleNamespace(load=lambda: SimpleNamespace(namespace_session_name='ccb-test')))
    monkeypatch.setattr('platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
                        lambda **kwargs: {'ok': True, 'warnings': []})
    monkeypatch.setattr(handlers, '_daemon_running_and_backend',
                        lambda context: (running, 'herdr' if running else None))
    monkeypatch.setattr(handlers, '_wait_for_ccbd_mounted',
                        lambda context: (ready, 'mounted' if ready else 'starting'))
    return context


def test_reopen_does_not_dispatch_start_or_restore(monkeypatch, tmp_path):
    context = setup_open(monkeypatch, tmp_path)
    starts = []
    monkeypatch.setattr(handlers, 'handle_start', lambda *args: starts.append(args) or 0)
    command = ParsedHerdrOpenCommand(project=None, no_attach=True, wait_ready=True)
    for _ in range(3):
        assert handlers.handle_herdr_open(context, command, StringIO(), None) == 0
    assert starts == [], 'reopening must never inject another provider launch command'


def test_readiness_timeout_is_a_failure(monkeypatch, tmp_path):
    context = setup_open(monkeypatch, tmp_path, running=False, ready=False)
    monkeypatch.setattr(handlers, 'handle_start', lambda *args: 0)
    command = ParsedHerdrOpenCommand(project=None, no_attach=True, wait_ready=True)
    assert handlers.handle_herdr_open(context, command, StringIO(), None) != 0


def attach_adapter(monkeypatch, run):
    adapter = HerdrCliRequestAdapter(session_name='ccb-test', herdr_executable='herdr',
                                     run_fn=run, foreground_run_fn=run, which_fn=lambda _: 'herdr')
    monkeypatch.setattr(adapter, '_resolve_logical_workspace', lambda **kw: {'workspace_id': 'w1'})
    monkeypatch.setattr(adapter, '_command', lambda *a, **kw: None)
    return adapter


def test_attach_failure_is_not_success(monkeypatch):
    def run(command, **kwargs):
        raise subprocess.CalledProcessError(7, command)
    adapter = attach_adapter(monkeypatch, run)
    with pytest.raises(MuxCommandErrorV2):
        adapter('attach_namespace', {'namespace_id': 'w1'})


def test_interactive_attach_has_no_five_second_lifetime(monkeypatch):
    def run(command, **kwargs):
        assert kwargs.get('timeout') is None, 'interactive session must survive past five seconds'
        return subprocess.CompletedProcess(command, 0)
    adapter = attach_adapter(monkeypatch, run)
    assert adapter('attach_namespace', {'namespace_id': 'w1'})['attached'] is True


def test_attach_preserves_console_through_runtime_wrapper(monkeypatch):
    from terminal_runtime import api
    monkeypatch.setattr(api, '_subprocess_kwargs', lambda: {'creationflags': 0x08000000})
    calls = []
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: calls.append(kw) or subprocess.CompletedProcess(a, 0))
    adapter = attach_adapter(monkeypatch, api._run)
    adapter._foreground_run_fn = subprocess.run
    adapter('attach_namespace', {'namespace_id': 'w1'})
    assert calls[0].get('creationflags', 0) == 0


@pytest.mark.parametrize('foreground,shell,allowed', [(20, 10, False), (10, 10, True), (None, 10, False)])
def test_respawn_only_sends_command_to_idle_shell(monkeypatch, foreground, shell, allowed):
    runs = []
    adapter = attach_adapter(monkeypatch, lambda *a, **kw: None)
    monkeypatch.setattr(adapter, '_pane_process_info', lambda payload: {
        'foreground_pid': foreground, 'process_info': {'shell_pid': shell}})
    monkeypatch.setattr(adapter, '_command', lambda *a, **kw: runs.append(a))
    if allowed:
        adapter('respawn_pane', {'pane_id': 'w1:p1', 'command': ['python', 'launch.py']})
        assert len(runs) == 1
    else:
        with pytest.raises(MuxCommandErrorV2, match='idle shell'):
            adapter('respawn_pane', {'pane_id': 'w1:p1', 'command': ['python', 'launch.py']})
        assert runs == []


def test_open_lock_serializes_real_processes_and_releases_on_exit(tmp_path):
    from platforms.windows.herdr.bootstrap import project_open_lock
    code = """
import sys,time
sys.path.insert(0, sys.argv[1])
from platforms.windows.herdr.bootstrap import project_open_lock
with project_open_lock(sys.argv[2]):
    print('locked', flush=True)
    time.sleep(15)
"""
    proc = subprocess.Popen([sys.executable, '-u', '-c', code,
                             str(Path(__file__).resolve().parents[1] / 'lib'), str(tmp_path)],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == 'locked'
        with pytest.raises(TimeoutError):
            with project_open_lock(tmp_path, timeout_s=.2):
                pytest.fail('overlapping startup acquired the same lock')
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    with project_open_lock(tmp_path, timeout_s=1):
        pass


@pytest.mark.parametrize('responsive', [False, True])
def test_mounted_file_requires_live_namespace_ping(monkeypatch, responsive):
    from ccbd import socket_client
    monkeypatch.setattr('ccbd.services.lifecycle.CcbdLifecycleStore',
                        lambda paths: SimpleNamespace(load=lambda: SimpleNamespace(phase='mounted')))
    def ping(name):
        if not responsive:
            raise socket_client.CcbdClientError('timed out')
        return {'mount_state': 'mounted', 'namespace_backend_impl': 'herdr', 'namespace_ui_attachable': True}
    monkeypatch.setattr(socket_client, 'CcbdClient', lambda *a, **kw: SimpleNamespace(ping=ping))
    context = SimpleNamespace(paths=SimpleNamespace(ccbd_socket_path='test-only'))
    assert handlers._wait_for_ccbd_mounted(context, timeout_s=.03, sleep_s=.001)[0] is responsive


def test_busy_live_daemon_is_reconnect_not_another_start(monkeypatch):
    monkeypatch.setattr('cli.services.daemon.inspect_daemon',
                        lambda context: (None, None, SimpleNamespace(pid_alive=True, socket_connectable=False)))
    monkeypatch.setattr('ccbd.services.project_namespace_state_runtime.stores.ProjectNamespaceStateStore',
                        lambda paths: SimpleNamespace(load=lambda: SimpleNamespace(backend_impl='herdr')))
    assert handlers._daemon_running_and_backend(SimpleNamespace(paths=None)) == (True, 'herdr')


def test_requested_session_does_not_fall_back_to_another_project(monkeypatch):
    from platforms.windows.herdr import bootstrap
    seen = []
    monkeypatch.setattr(bootstrap, '_discover_running_ccb_sessions', lambda exe: ['ccb-other-user-project'])
    def query(exe, session):
        seen.append(session)
        return {'running': session == 'ccb-other-user-project'}
    monkeypatch.setattr(bootstrap, 'query_herdr_server_status', query)
    assert bootstrap._resolve_running_server('herdr', 'ccb-this-project') == (None, None, None)
    assert seen == ['ccb-this-project']


@pytest.mark.parametrize('running', [False, True])
def test_owned_session_is_used_for_both_lookup_and_start(monkeypatch, tmp_path, running):
    context = setup_open(monkeypatch, tmp_path, running=running)
    context.paths = SimpleNamespace(ccbd_tmux_session_name='ccb-default')
    monkeypatch.setattr('ccbd.services.project_namespace_state_runtime.stores.ProjectNamespaceStateStore',
                        lambda paths: SimpleNamespace(load=lambda: SimpleNamespace(namespace_session_name='custom-session')))
    calls = []
    monkeypatch.setattr('platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
                        lambda **kw: calls.append(kw) or {'ok': True})
    monkeypatch.setattr(handlers, 'handle_start', lambda *a: 0)
    expected = 'custom-session' if running else 'ccb-default'
    command = ParsedHerdrOpenCommand(project=None, no_attach=True, herdr_session=expected)
    assert handlers.handle_herdr_open(context, command, StringIO(), None) == 0
    assert calls[0]['herdr_session'] == calls[0]['start_session'] == expected
    assert calls[0]['auto_start_server'] is (not running)


def test_reconnect_uses_recorded_custom_session_and_rejects_switch(monkeypatch, tmp_path):
    context = setup_open(monkeypatch, tmp_path)
    context.paths = SimpleNamespace(ccbd_tmux_session_name='ccb-default')
    monkeypatch.setattr('ccbd.services.project_namespace_state_runtime.stores.ProjectNamespaceStateStore',
                        lambda paths: SimpleNamespace(load=lambda: SimpleNamespace(namespace_session_name='custom-session')))
    calls = []
    monkeypatch.setattr('platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
                        lambda **kw: calls.append(kw) or {'ok': True})
    command = ParsedHerdrOpenCommand(project=None, no_attach=True)
    assert handlers.handle_herdr_open(context, command, StringIO(), None) == 0
    assert calls[0]['herdr_session'] == 'custom-session'
    calls.clear()
    with pytest.raises(RuntimeError, match='already uses Herdr session'):
        handlers.handle_herdr_open(context, ParsedHerdrOpenCommand(
            project=None, no_attach=True, herdr_session='other-session'), StringIO(), None)
    assert not calls


def test_cold_open_rejects_unsupported_session_before_starting_server(monkeypatch, tmp_path):
    context = setup_open(monkeypatch, tmp_path, running=False)
    context.paths = SimpleNamespace(ccbd_tmux_session_name='ccb-default')
    monkeypatch.setattr('platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
                        lambda **kw: pytest.fail('must not create a foreign server'))
    with pytest.raises(RuntimeError, match='custom session names are not supported'):
        handlers.handle_herdr_open(context, ParsedHerdrOpenCommand(
            project=None, no_attach=True, herdr_session='other-session'), StringIO(), None)
