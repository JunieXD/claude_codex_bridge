from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

import process_background
from cli.models import ParsedHerdrOpenCommand
from cli.parser import CliParser, CliUsageError
from platforms.windows.herdr.bootstrap import ensure_herdr_bootstrap_env
from platforms.windows.herdr.common import query_herdr_server_status, resolve_herdr_executable


@pytest.fixture()
def parser() -> CliParser:
    return CliParser()


def _patch_process_background_os_name(monkeypatch, name: str) -> None:
    os_proxy = SimpleNamespace(**vars(process_background.os))
    os_proxy.name = name
    monkeypatch.setattr(process_background, 'os', os_proxy)


# --- parse_herdr ---------------------------------------------------------


def test_parse_herdr_open_defaults(parser: CliParser) -> None:
    parsed = parser.parse(['herdr', 'open'])
    assert parsed == ParsedHerdrOpenCommand(project=None, kind='herdr-open')


def test_parse_herdr_open_with_flags(parser: CliParser) -> None:
    parsed = parser.parse(
        ['herdr', 'open', '--no-attach', '--herdr-session', 'sess-1', '--herdr-exe', '/x/herdr.exe']
    )
    assert parsed == ParsedHerdrOpenCommand(
        project=None,
        herdr_exe='/x/herdr.exe',
        herdr_session='sess-1',
        no_attach=True,
        wait_ready=False,
        kind='herdr-open',
    )


def test_parse_herdr_open_wait_ready(parser: CliParser) -> None:
    parsed = parser.parse(['herdr', 'open', '--no-attach', '--wait-ready'])
    assert parsed == ParsedHerdrOpenCommand(
        project=None,
        no_attach=True,
        wait_ready=True,
        kind='herdr-open',
    )


def test_parse_herdr_requires_subcommand(parser: CliParser) -> None:
    with pytest.raises(CliUsageError, match='herdr supports'):
        parser.parse(['herdr'])


def test_parse_herdr_rejects_unknown_subcommand(parser: CliParser) -> None:
    with pytest.raises(CliUsageError, match='herdr supports'):
        parser.parse(['herdr', 'attach'])


# --- ensure_herdr_bootstrap_env ------------------------------------------


def test_bootstrap_rejects_missing_executable(monkeypatch) -> None:
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: None,
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is False
    assert 'Herdr executable not found' in str(result['reason'])


def _patch_discovery_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._discover_running_ccb_sessions',
        lambda exe: [],
    )


def test_bootstrap_rejects_unqueryable_server(monkeypatch) -> None:
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    _patch_discovery_empty(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        lambda exe, session=None: None,
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is False
    assert 'Failed to query Herdr server status' in str(result['reason'])


def test_bootstrap_rejects_stopped_server(monkeypatch) -> None:
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    _patch_discovery_empty(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        lambda exe, session=None: {'status': 'stopped', 'running': False, 'compatible': True},
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is False
    assert 'server is not running' in str(result['reason'])


def test_bootstrap_rejects_incompatible_protocol(monkeypatch) -> None:
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    _patch_discovery_empty(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        lambda exe, session=None: {'status': 'running', 'running': True, 'compatible': False, 'protocol': 3},
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is False
    assert 'not compatible' in str(result['reason'])


def _bootstrap_success_mocks(monkeypatch, *, status=None):
    monkeypatch.delenv('CCB_HERDR_EXE', raising=False)
    monkeypatch.delenv('CCB_HERDR_SESSION', raising=False)
    monkeypatch.delenv('CCB_HERDR_CAPABILITY_REPORT', raising=False)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    _patch_discovery_empty(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        lambda exe, session=None: status
        or {
            'status': 'running',
            'running': True,
            'compatible': True,
            'protocol': 19,
            'session': 'sess-live',
        },
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._probe_herdr_read_capabilities',
        lambda exe, session=None: {
            'session_attach': True,
            'workspace_list': True,
            'pane_list': True,
        },
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._write_capability_report',
        lambda report: 'C:/tmp/ccb-herdr-capability-test.json',
    )


def test_bootstrap_sets_env_and_succeeds(monkeypatch) -> None:
    _bootstrap_success_mocks(monkeypatch)
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is True
    assert result['herdr_exe'] == '/x/herdr.exe'
    assert result['herdr_session'] == 'sess-live'
    assert os.environ['CCB_HERDR_EXE'] == '/x/herdr.exe'
    assert os.environ['CCB_HERDR_SESSION'] == 'sess-live'
    assert os.environ['CCB_HERDR_CAPABILITY_REPORT'] == 'C:/tmp/ccb-herdr-capability-test.json'


def test_bootstrap_prefers_explicit_session(monkeypatch) -> None:
    _bootstrap_success_mocks(monkeypatch)
    result = ensure_herdr_bootstrap_env(herdr_session='sess-explicit')
    assert result['ok'] is True
    assert result['herdr_session'] == 'sess-explicit'
    assert os.environ['CCB_HERDR_SESSION'] == 'sess-explicit'


def test_bootstrap_rejects_failed_read_probes(monkeypatch) -> None:
    _bootstrap_success_mocks(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._probe_herdr_read_capabilities',
        lambda exe, session=None: {
            'session_attach': True,
            'workspace_list': False,
            'pane_list': True,
        },
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is False
    assert 'workspace_list' in str(result['reason'])


# --- P0: auto-start server when nothing is running ------------------------


def _auto_start_resolve(monkeypatch) -> list[str]:
    """Patch _resolve_running_server to be empty before and live after start."""
    calls: list[str] = []
    started: list[str] = []
    live_server = {
        'status': 'running',
        'running': True,
        'compatible': True,
        'protocol': 19,
        'session': 'sess-live',
    }

    def _resolve(exe, preferred_session):
        calls.append('resolve')
        if started:
            return (live_server, 'sess-live', None)
        return (None, None, None)

    def _start(exe, session):
        started.append(session)
        return {'ok': True, 'herdr_session': session}

    monkeypatch.setattr('platforms.windows.herdr.bootstrap._resolve_running_server', _resolve)
    monkeypatch.setattr('platforms.windows.herdr.bootstrap._start_herdr_server', _start)
    return started


def test_bootstrap_auto_starts_server_when_nothing_running(monkeypatch) -> None:
    """P0: with auto_start_server, a missing server is started instead of failing."""
    _bootstrap_success_mocks(monkeypatch)
    started = _auto_start_resolve(monkeypatch)
    result = ensure_herdr_bootstrap_env(auto_start_server=True, start_session='ccb-proj-abc12345')
    assert result['ok'] is True
    assert started == ['ccb-proj-abc12345']
    assert os.environ['CCB_HERDR_SESSION'] == 'sess-live'


def test_bootstrap_auto_start_uses_default_session(monkeypatch) -> None:
    """P0: auto-start falls back to default session when none given."""
    _bootstrap_success_mocks(monkeypatch)
    started = _auto_start_resolve(monkeypatch)
    result = ensure_herdr_bootstrap_env(auto_start_server=True)
    assert result['ok'] is True
    # No preferred session env is set (_bootstrap_success_mocks clears it), so
    # the fallback is _DEFAULT_HERDR_SESSION ('ccb-herdr').
    assert started == ['ccb-herdr']


def test_bootstrap_auto_start_propagates_start_failure(monkeypatch) -> None:
    """P0: a failed auto-start surfaces the start error instead of proceeding."""
    _bootstrap_success_mocks(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._resolve_running_server',
        lambda exe, preferred_session: (None, None, None),
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._start_herdr_server',
        lambda exe, session: {'ok': False, 'reason': 'boom'},
    )
    result = ensure_herdr_bootstrap_env(auto_start_server=True)
    assert result['ok'] is False
    assert 'boom' in str(result['reason'])


def test_bootstrap_auto_start_still_fails_when_not_reachable(monkeypatch) -> None:
    """P0: if auto-start does not make the server reachable, fail with guidance."""
    _bootstrap_success_mocks(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._resolve_running_server',
        lambda exe, preferred_session: (None, None, None),
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._start_herdr_server',
        lambda exe, session: {'ok': True, 'herdr_session': session},
    )
    result = ensure_herdr_bootstrap_env(auto_start_server=True)
    assert result['ok'] is False
    assert 'started but did not become reachable' in str(result['reason'])


def test_build_capability_report_covers_known_capabilities() -> None:
    from platforms.windows.herdr.bootstrap import _build_capability_report

    report = _build_capability_report(
        {'session_attach': True, 'workspace_list': True, 'pane_list': True}
    )
    assert report['verdict'] == 'pass'
    assert report['adapter_recommendation'] == 'continue'
    assert report['windows_beta_gaps'] == []
    assert report['blocking_gaps'] == []
    assert report['command_status']['session_attach'] == 'supported'
    assert report['command_status']['kill_pane'] == 'supported'


# --- handle_herdr_open daemon conflict ------------------------------------


def _stub_bootstrap_ok(monkeypatch) -> None:
    monkeypatch.setattr('platforms.windows.herdr.entrypoint._open_session',
                        lambda *a, **kw: 'ccb-test')
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
        lambda **kwargs: {'ok': True, 'warnings': []},
    )


def test_handle_herdr_open_rejects_conflicting_tmux_daemon(monkeypatch, capsys) -> None:
    from platforms.windows.herdr.entrypoint import handle_herdr_open

    _stub_bootstrap_ok(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint._daemon_running_and_backend',
        lambda context: (True, 'tmux'),
    )
    rc = handle_herdr_open(None, ParsedHerdrOpenCommand(project=None), sys.stdout, None)
    assert rc == 1
    err = capsys.readouterr().err
    assert 'tmux' in err
    assert 'ccb kill' in err


def test_handle_herdr_open_rejects_daemon_with_unknown_backend(monkeypatch, capsys) -> None:
    from platforms.windows.herdr.entrypoint import handle_herdr_open

    _stub_bootstrap_ok(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint._daemon_running_and_backend',
        lambda context: (True, None),
    )
    rc = handle_herdr_open(None, ParsedHerdrOpenCommand(project=None), sys.stdout, None)
    assert rc == 1
    assert 'ccb kill' in capsys.readouterr().err


def test_handle_herdr_open_proceeds_when_daemon_is_herdr(monkeypatch) -> None:
    from platforms.windows.herdr.entrypoint import handle_herdr_open

    _stub_bootstrap_ok(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint._daemon_running_and_backend',
        lambda context: (True, 'herdr'),
    )
    monkeypatch.setattr('platforms.windows.herdr.entrypoint._wait_for_ccbd_mounted', lambda context: (True, 'mounted'))
    started: dict[str, bool] = {}

    def _fake_handle_start(context, command, out, services) -> int:
        started['started'] = True
        return 0

    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint.handle_start',
        _fake_handle_start,
    )
    rc = handle_herdr_open(None, ParsedHerdrOpenCommand(project=None), sys.stdout, None)
    assert rc == 0
    assert not started


def test_handle_herdr_open_wait_ready_blocks_until_mounted(monkeypatch) -> None:
    """P1: --wait-ready makes handle_herdr_open poll ccbd lifecycle to mounted."""
    from platforms.windows.herdr.entrypoint import handle_herdr_open

    _stub_bootstrap_ok(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint._daemon_running_and_backend',
        lambda context: (True, 'herdr'),
    )

    def _fake_handle_start(context, command, out, services) -> int:
        return 0

    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint.handle_start',
        _fake_handle_start,
    )
    waited: list[object] = []

    class _FakeLifecycle:
        phase = 'mounted'

    class _FakeStore:
        def load(self):
            return _FakeLifecycle()

    def _fake_wait(context, *args, **kwargs):
        waited.append(context)
        return (True, 'mounted')

    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint._wait_for_ccbd_mounted',
        _fake_wait,
    )
    command = ParsedHerdrOpenCommand(project=None, no_attach=True, wait_ready=True)
    rc = handle_herdr_open(None, command, sys.stdout, None)
    assert rc == 0
    assert waited, '--wait-ready must call the ccbd-mounted wait'


def test_handle_herdr_open_no_attach_is_scoped_to_start(monkeypatch) -> None:
    from platforms.windows.herdr.entrypoint import handle_herdr_open

    _stub_bootstrap_ok(monkeypatch)
    monkeypatch.delenv('CCB_NO_ATTACH', raising=False)
    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint._daemon_running_and_backend',
        lambda context: (False, None),
    )
    seen: list[str | None] = []

    def _fake_handle_start(context, command, out, services) -> int:
        del context, command, out, services
        seen.append(os.environ.get('CCB_NO_ATTACH'))
        return 0

    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint.handle_start',
        _fake_handle_start,
    )

    rc = handle_herdr_open(
        None,
        ParsedHerdrOpenCommand(project=None, no_attach=True),
        sys.stdout,
        None,
    )

    assert rc == 0
    assert seen == ['1']
    assert 'CCB_NO_ATTACH' not in os.environ


def test_handle_herdr_open_wait_ready_reports_timeout(monkeypatch, capsys) -> None:
    """P1: --wait-ready timeout surfaces a diagnostic and fails."""
    from platforms.windows.herdr.entrypoint import handle_herdr_open

    _stub_bootstrap_ok(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint._daemon_running_and_backend',
        lambda context: (True, 'herdr'),
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint.handle_start',
        lambda context, command, out, services: 0,
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.entrypoint._wait_for_ccbd_mounted',
        lambda context, *args, **kwargs: (False, 'starting'),
    )
    command = ParsedHerdrOpenCommand(project=None, no_attach=True, wait_ready=True)
    rc = handle_herdr_open(None, command, sys.stdout, None)
    assert rc == 1
    assert 'not ready after waiting' in capsys.readouterr().err


def test_ccbd_herdr_session_name_defensive() -> None:
    """P0: _ccbd_herdr_session_name tolerates a missing context/paths."""
    from platforms.windows.herdr.entrypoint import _ccbd_herdr_session_name

    assert _ccbd_herdr_session_name(None) is None
    assert _ccbd_herdr_session_name(SimpleNamespace(paths=None)) is None
    assert _ccbd_herdr_session_name(
        SimpleNamespace(paths=SimpleNamespace(ccbd_tmux_session_name='ccb-proj-abc12345'))
    ) == 'ccb-proj-abc12345'


def test_daemon_running_and_backend_detects_herdr(monkeypatch) -> None:
    from platforms.windows.herdr.entrypoint import _daemon_running_and_backend

    class _FakeInspection:
        pid_alive = True
        socket_connectable = True

    class _FakeState:
        backend_impl = 'herdr'

    class _FakeStore:
        def load(self):
            return _FakeState()

    monkeypatch.setattr(
        'cli.services.daemon.inspect_daemon',
        lambda context: (None, None, _FakeInspection()),
    )
    monkeypatch.setattr(
        'ccbd.services.project_namespace_state_runtime.stores.ProjectNamespaceStateStore',
        lambda paths: _FakeStore(),
    )
    running, backend = _daemon_running_and_backend(SimpleNamespace(paths=SimpleNamespace()))
    assert running is True
    assert backend == 'herdr'


def test_daemon_running_and_backend_fails_closed_on_inspection_error(
    monkeypatch,
) -> None:
    """DEC-3: generic inspection errors → fail-closed (treat as potential conflict)."""
    from platforms.windows.herdr.entrypoint import _daemon_running_and_backend

    def _boom(context):
        raise RuntimeError('no lease')

    monkeypatch.setattr('cli.services.daemon.inspect_daemon', _boom)
    running, backend = _daemon_running_and_backend(SimpleNamespace(paths=SimpleNamespace()))
    assert running is True, 'DEC-3: inspection error should be fail-closed'
    assert backend is None


# --- resolve_herdr_executable --------------------------------------------


def test_resolve_herdr_explicit_existing(tmp_path) -> None:
    exe = tmp_path / 'herdr.exe'
    exe.write_text('')
    assert resolve_herdr_executable(explicit=str(exe)) == str(exe)


def test_resolve_herdr_via_env(monkeypatch, tmp_path) -> None:
    exe = tmp_path / 'herdr.exe'
    exe.write_text('')
    monkeypatch.setenv('CCB_HERDR_EXE', str(exe))
    assert resolve_herdr_executable() == str(exe)


def test_resolve_herdr_nonexistent_explicit_falls_back(monkeypatch) -> None:
    monkeypatch.delenv('CCB_HERDR_EXE', raising=False)
    monkeypatch.setattr('platforms.windows.herdr.common.shutil.which', lambda name: None)
    monkeypatch.setattr(
        'platforms.windows.herdr.common.os.path.isfile',
        lambda path: False,
    )
    assert resolve_herdr_executable(explicit='C:/nonexistent/herdr.exe') is None


# --- query_herdr_server_status --------------------------------------------


def test_query_herdr_server_status_running(monkeypatch) -> None:
    import subprocess

    class _FakeResult:
        returncode = 0
        stdout = '{"status":"running","running":true,"compatible":true,"protocol":19}'

    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: _FakeResult())
    payload = query_herdr_server_status('/x/herdr.exe')
    assert payload is not None
    assert payload['running'] is True
    assert payload['protocol'] == 19


def test_query_herdr_server_status_nonzero_exit(monkeypatch) -> None:
    import subprocess

    class _FakeResult:
        returncode = 1
        stdout = ''

    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: _FakeResult())
    assert query_herdr_server_status('/x/herdr.exe') is None


def test_query_herdr_server_status_malformed_json(monkeypatch) -> None:
    import subprocess

    class _FakeResult:
        returncode = 0
        stdout = 'not-json'

    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: _FakeResult())
    assert query_herdr_server_status('/x/herdr.exe') is None


def test_query_herdr_server_status_passes_session_flag(monkeypatch) -> None:
    """``session`` is forwarded as ``--session <name>`` to the CLI."""
    import subprocess

    captured: dict[str, object] = {}

    class _FakeResult:
        returncode = 0
        stdout = '{"running":true}'

    def _fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        return _FakeResult()

    monkeypatch.setattr(subprocess, 'run', _fake_run)
    payload = query_herdr_server_status('/x/herdr.exe', session='ccb-proj-abc')
    assert payload is not None
    assert captured['cmd'] == [
        '/x/herdr.exe',
        'status',
        'server',
        '--json',
        '--session',
        'ccb-proj-abc',
    ]


def test_query_herdr_server_status_without_session_has_no_flag(monkeypatch) -> None:
    """Without ``session`` the command must not carry ``--session``."""
    import subprocess

    captured: dict[str, object] = {}

    class _FakeResult:
        returncode = 0
        stdout = '{"running":true}'

    def _fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        return _FakeResult()

    monkeypatch.setattr(subprocess, 'run', _fake_run)
    query_herdr_server_status('/x/herdr.exe')
    assert captured['cmd'] == ['/x/herdr.exe', 'status', 'server', '--json']


def test_query_herdr_server_status_hides_windows_console(monkeypatch) -> None:
    """Startup hot-path Herdr status probes must not create flash consoles."""
    import subprocess

    captured: dict[str, object] = {}

    class _FakeResult:
        returncode = 0
        stdout = '{"running":true}'

    def _fake_run(cmd, **kwargs):
        del cmd
        captured.update(kwargs)
        return _FakeResult()

    _patch_process_background_os_name(monkeypatch, 'nt')
    monkeypatch.setattr(process_background.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, 'run', _fake_run)

    assert query_herdr_server_status('/x/herdr.exe') is not None
    assert captured['creationflags'] == 0x08000000


def test_discover_running_ccb_sessions_parses_running(monkeypatch) -> None:
    """``herdr session list`` running entries are parsed, ``ccb-`` filtered."""
    import json as _json
    import subprocess

    from platforms.windows.herdr.bootstrap import _discover_running_ccb_sessions

    payload = {
        'sessions': [
            {'name': 'default', 'running': False},
            {'name': 'ccb-avaprintdesigner-575a971f', 'running': True},
            {'name': 'ccb-herdr', 'running': False},
            {'name': 'other-session', 'running': True},
        ]
    }

    class _FakeResult:
        returncode = 0
        stdout = _json.dumps(payload)

    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: _FakeResult())
    assert _discover_running_ccb_sessions('/x/herdr.exe') == [
        'ccb-avaprintdesigner-575a971f'
    ]


def test_discover_running_ccb_sessions_hides_windows_console(monkeypatch) -> None:
    import json as _json
    import subprocess

    from platforms.windows.herdr.bootstrap import _discover_running_ccb_sessions

    captured: dict[str, object] = {}

    class _FakeResult:
        returncode = 0
        stdout = _json.dumps({'sessions': []})

    def _fake_run(cmd, **kwargs):
        del cmd
        captured.update(kwargs)
        return _FakeResult()

    _patch_process_background_os_name(monkeypatch, 'nt')
    monkeypatch.setattr(process_background.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, 'run', _fake_run)

    assert _discover_running_ccb_sessions('/x/herdr.exe') == []
    assert captured['creationflags'] == 0x08000000


def test_probe_herdr_read_capabilities_hides_windows_console(monkeypatch) -> None:
    import subprocess

    from platforms.windows.herdr.bootstrap import _probe_herdr_read_capabilities

    captured_flags: list[int] = []

    class _FakeResult:
        returncode = 0
        stdout = '{}'

    def _fake_run(cmd, **kwargs):
        del cmd
        captured_flags.append(int(kwargs['creationflags']))
        return _FakeResult()

    _patch_process_background_os_name(monkeypatch, 'nt')
    monkeypatch.setattr(process_background.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, 'run', _fake_run)

    _probe_herdr_read_capabilities('/x/herdr.exe', session='ccb-proj-abc')
    assert captured_flags == [0x08000000, 0x08000000, 0x08000000]


# ---------------------------------------------------------------------------
# session-scoped server discovery (CCB 8.5.2 regression)
# ---------------------------------------------------------------------------

def test_bootstrap_uses_running_session_when_global_stopped(monkeypatch) -> None:
    """A running session-scoped herdr server must be accepted even when the
    global server (``herdr status server --json`` without ``--session``)
    reports not running — the real AvaPrintDesigner failure mode."""
    monkeypatch.delenv('CCB_HERDR_EXE', raising=False)
    monkeypatch.delenv('CCB_HERDR_SESSION', raising=False)
    monkeypatch.delenv('CCB_HERDR_CAPABILITY_REPORT', raising=False)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    # Discovery surfaces the live CCB session.
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._discover_running_ccb_sessions',
        lambda exe: ['ccb-avaprintdesigner-575a971f'],
    )
    queried: list[str | None] = []

    def _fake_status(exe, session=None):
        queried.append(session)
        if session is None:
            return {'status': 'not_running', 'running': False, 'compatible': None}
        return {
            'status': 'running',
            'running': True,
            'compatible': True,
            'protocol': 19,
            'session': session,
        }

    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        _fake_status,
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._probe_herdr_read_capabilities',
        lambda exe, session=None: {
            'session_attach': True,
            'workspace_list': True,
            'pane_list': True,
        },
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._write_capability_report',
        lambda report: '/tmp/cap.json',
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is True, f'session-scoped server should be accepted: {result}'
    assert result['herdr_session'] == 'ccb-avaprintdesigner-575a971f'
    assert os.environ['CCB_HERDR_SESSION'] == 'ccb-avaprintdesigner-575a971f'
    # The discovered session must have been probed directly.
    assert queried == ['ccb-avaprintdesigner-575a971f']


def test_bootstrap_still_rejects_when_only_global_stopped(monkeypatch) -> None:
    """When nothing (session-scoped or global) is running, the bootstrap keeps
    rejecting with the actionable "server is not running" reason."""
    monkeypatch.delenv('CCB_HERDR_EXE', raising=False)
    monkeypatch.delenv('CCB_HERDR_SESSION', raising=False)
    monkeypatch.delenv('CCB_HERDR_CAPABILITY_REPORT', raising=False)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    _patch_discovery_empty(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        lambda exe, session=None: {'status': 'not_running', 'running': False},
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is False
    assert 'server is not running' in str(result['reason'])


# ---------------------------------------------------------------------------
# ITEM-2 fix 3: nested server shape unwrapping
# ---------------------------------------------------------------------------

def test_bootstrap_handles_nested_result_server_shape(monkeypatch) -> None:
    """Nested {"result":{"server":{"running":true,...}}} unwraps correctly."""
    monkeypatch.delenv('CCB_HERDR_EXE', raising=False)
    monkeypatch.delenv('CCB_HERDR_SESSION', raising=False)
    monkeypatch.delenv('CCB_HERDR_CAPABILITY_REPORT', raising=False)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    _patch_discovery_empty(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        lambda exe, session=None: {
            'result': {
                'server': {
                    'running': True,
                    'compatible': True,
                    'protocol': 19,
                    'session': 'sess-nested',
                }
            }
        },
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._probe_herdr_read_capabilities',
        lambda exe, session=None: {'session_attach': True, 'workspace_list': True, 'pane_list': True},
    )
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap._write_capability_report',
        lambda report: '/tmp/cap.json',
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is True, f'Nested shape should succeed, got: {result}'
    assert result['herdr_session'] == 'sess-nested'


def test_bootstrap_nested_shape_rejects_stopped(monkeypatch) -> None:
    """Nested shape with running=False is correctly rejected."""
    monkeypatch.delenv('CCB_HERDR_EXE', raising=False)
    monkeypatch.delenv('CCB_HERDR_SESSION', raising=False)
    monkeypatch.delenv('CCB_HERDR_CAPABILITY_REPORT', raising=False)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    _patch_discovery_empty(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        lambda exe, session=None: {
            'result': {
                'server': {
                    'running': False,
                    'compatible': True,
                }
            }
        },
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is False
    assert 'server is not running' in str(result['reason'])


def test_bootstrap_nested_shape_rejects_incompatible(monkeypatch) -> None:
    """Nested shape with compatible=False is correctly rejected."""
    monkeypatch.delenv('CCB_HERDR_EXE', raising=False)
    monkeypatch.delenv('CCB_HERDR_SESSION', raising=False)
    monkeypatch.delenv('CCB_HERDR_CAPABILITY_REPORT', raising=False)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.resolve_herdr_executable',
        lambda explicit=None: '/x/herdr.exe',
    )
    _patch_discovery_empty(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.query_herdr_server_status',
        lambda exe, session=None: {
            'result': {
                'server': {
                    'running': True,
                    'compatible': False,
                    'protocol': 2,
                }
            }
        },
    )
    result = ensure_herdr_bootstrap_env()
    assert result['ok'] is False
    assert 'not compatible' in str(result['reason'])


# ---------------------------------------------------------------------------
# ITEM-2 fix 2: XDG platform gate
# ---------------------------------------------------------------------------

def test_herdr_command_env_clears_xdg_on_windows(monkeypatch) -> None:
    """XDG_* is cleared on Windows, HERDR_CONFIG_PATH is set."""
    import sys as _sys
    from platforms.windows.herdr.common import herdr_command_env

    if _sys.platform != 'win32':
        import pytest
        pytest.skip('test only meaningful on Windows')

    monkeypatch.setenv('XDG_CONFIG_HOME', '/fake/xdg/config')
    monkeypatch.setenv('XDG_CACHE_HOME', '/fake/xdg/cache')
    monkeypatch.setenv('XDG_STATE_HOME', '/fake/xdg/state')
    monkeypatch.delenv('HERDR_CONFIG_PATH', raising=False)

    env = herdr_command_env()
    assert 'XDG_CONFIG_HOME' not in env
    assert 'XDG_CACHE_HOME' not in env
    assert 'XDG_STATE_HOME' not in env
    assert 'HERDR_CONFIG_PATH' in env


def test_herdr_command_env_preserves_xdg_on_non_windows(monkeypatch) -> None:
    """XDG_* is preserved on non-Windows platforms."""
    from platforms.windows.herdr.common import herdr_command_env

    monkeypatch.setattr('sys.platform', 'linux')
    monkeypatch.setenv('XDG_CONFIG_HOME', '/fake/xdg/config')
    monkeypatch.setenv('XDG_CACHE_HOME', '/fake/xdg/cache')

    env = herdr_command_env()
    assert env.get('XDG_CONFIG_HOME') == '/fake/xdg/config'
    assert env.get('XDG_CACHE_HOME') == '/fake/xdg/cache'
    # HERDR_CONFIG_PATH should NOT be forced on non-Windows
    assert 'HERDR_CONFIG_PATH' not in env or env['HERDR_CONFIG_PATH'] == os.environ.get('HERDR_CONFIG_PATH', '')


# --- handle_start herdr evidence auto-probe (installed ccb parity) -------


def _clear_herdr_report_env(monkeypatch) -> None:
    monkeypatch.delenv('CCB_HERDR_CAPABILITY_REPORT', raising=False)


def test_start_auto_probes_herdr_evidence_when_backend_herdr_and_report_missing(
    monkeypatch,
) -> None:
    """Installed `ccb` (bare start) with herdr backend probes and injects evidence."""
    from cli.phase2_runtime.handlers_start import _ensure_herdr_runtime_evidence

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')
    _clear_herdr_report_env(monkeypatch)
    called: dict[str, object] = {}
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
        lambda **kwargs: called.update(kwargs) or {'ok': True, 'warnings': []},
    )
    monkeypatch.setattr(
        'cli.phase2_runtime.handlers_start._herdr_capability_evidence_usable',
        lambda: False,
    )
    monkeypatch.setattr(
        'cli.phase2_runtime.handlers_start._is_herdr_relevant_platform',
        lambda: True,
    )
    _ensure_herdr_runtime_evidence(SimpleNamespace(paths=SimpleNamespace()))
    assert called.get('auto_start_server') is True


def test_start_skips_probe_when_backend_not_herdr(monkeypatch) -> None:
    from cli.phase2_runtime.handlers_start import _ensure_herdr_runtime_evidence

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'tmux')
    called: dict[str, object] = {}
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
        lambda **kwargs: called.update(kwargs) or {'ok': True},
    )
    _ensure_herdr_runtime_evidence(SimpleNamespace(paths=SimpleNamespace()))
    assert called == {}


def test_start_skips_probe_when_evidence_already_usable(monkeypatch) -> None:
    from cli.phase2_runtime.handlers_start import _ensure_herdr_runtime_evidence

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')
    monkeypatch.setenv('CCB_HERDR_CAPABILITY_REPORT', 'C:/tmp/report.json')
    called: dict[str, object] = {}
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
        lambda **kwargs: called.update(kwargs) or {'ok': True},
    )
    monkeypatch.setattr(
        'cli.phase2_runtime.handlers_start._herdr_capability_evidence_usable',
        lambda: True,
    )
    _ensure_herdr_runtime_evidence(SimpleNamespace(paths=SimpleNamespace()))
    assert called == {}


def test_herdr_capability_evidence_usable_detects_missing_and_invalid() -> None:
    from cli.phase2_runtime.handlers_start import _herdr_capability_evidence_usable

    # no env
    import os as _os

    _os.environ.pop('CCB_HERDR_CAPABILITY_REPORT', None)
    assert _herdr_capability_evidence_usable() is False


def test_start_probe_failure_is_non_fatal(monkeypatch) -> None:
    """A failed evidence probe must not raise — selection will fail-closed."""
    from cli.phase2_runtime.handlers_start import _ensure_herdr_runtime_evidence

    monkeypatch.setenv('CCB_RUNTIME_MUX_BACKEND', 'herdr')
    _clear_herdr_report_env(monkeypatch)
    monkeypatch.setattr(
        'platforms.windows.herdr.bootstrap.ensure_herdr_bootstrap_env',
        lambda **kwargs: {'ok': False, 'reason': 'boom'},
    )
    monkeypatch.setattr(
        'cli.phase2_runtime.handlers_start._herdr_capability_evidence_usable',
        lambda: False,
    )
    # Must not raise even though probe failed.
    _ensure_herdr_runtime_evidence(SimpleNamespace(paths=SimpleNamespace()))
