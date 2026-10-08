from io import StringIO
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from cli import entrypoint_runtime as shared_cli, phase2
from cli.phase2_runtime import handlers_start as shared_start
from platforms.windows.herdr import entrypoint as native


EARLY_HANDLERS = (
    'maybe_handle_sidebar_click_command', 'maybe_handle_sidebar_resize_sync_command',
    'maybe_handle_background_update_refresh_command', 'maybe_handle_mobile_host_serve_command',
    'maybe_handle_post_update_command', '_handle_help', '_handle_removed_commands',
    '_dispatch_auxiliary', '_dispatch_management', '_dispatch_rich', '_dispatch_tools',
    '_dispatch_theme', '_dispatch_roles', '_dispatch_auto_rich_start',
    'maybe_handle_startup_release_update',
)


@pytest.mark.parametrize('stop_at', (*EARLY_HANDLERS, None))
def test_windows_composition_preserves_shared_early_command_priority(monkeypatch, tmp_path, stop_at):
    events = []
    for name in EARLY_HANDLERS:
        def handler(*a, _name=name, **kw):
            events.append(_name)
            return 7 if _name == stop_at else None
        monkeypatch.setattr(shared_cli, name, handler)
    monkeypatch.setattr(shared_cli, 'maybe_handle_phase2', lambda *a, **kw: events.append('shared') or 19)
    monkeypatch.setattr(native, '_native_phase2', lambda *a, **kw: events.append('native') or 29)
    shared_functions = (shared_cli.run_cli_entrypoint, phase2.maybe_handle_phase2,
                        phase2._dispatch, shared_start.handle_start)
    kwargs = dict(version='test', script_root=tmp_path, cwd=tmp_path,
                  stdout=StringIO(), stderr=StringIO())
    expected = shared_cli.run_cli_entrypoint([], **kwargs)
    shared_events = list(events)
    events.clear()
    actual = native.run_native_cli_entrypoint([], **kwargs)
    if stop_at is None:
        assert (expected, actual) == (19, 29)
        assert events[:-1] == shared_events[:-1]
        assert events[-1] == 'native' and shared_events[-1] == 'shared'
    else:
        assert actual == expected == 7
        assert events == shared_events
    assert shared_functions == (shared_cli.run_cli_entrypoint, phase2.maybe_handle_phase2,
                                phase2._dispatch, shared_start.handle_start)


def test_native_phase2_keeps_bootstrap_order_and_standard_errors(monkeypatch, tmp_path):
    events = []
    context = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path))
    monkeypatch.setattr(phase2, '_build_context', lambda *a, **kw: events.append('context') or context)
    monkeypatch.setattr(phase2, 'ensure_bootstrap_project_config', lambda *a: events.append('bootstrap'))
    def fail(*args):
        events.append('dispatch')
        raise RuntimeError('native startup failed')
    monkeypatch.setattr(native, '_native_dispatch', fail)
    err = StringIO()
    assert native._native_phase2([], cwd=tmp_path, stdout=StringIO(), stderr=err) == 1
    assert events == ['context', 'bootstrap', 'dispatch']
    assert 'command_status: failed' in err.getvalue() and 'native startup failed' in err.getvalue()


def test_native_phase2_delegates_management_and_preserves_parse_errors(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(phase2, 'maybe_handle_phase2', lambda *a, **kw: calls.append(a) or 9)
    assert native._native_phase2(['ping', 'all'], cwd=tmp_path) == 9
    assert calls == [(['ping', 'all'],)]
    err = StringIO()
    assert native._native_phase2(['--invalid-native-option'], stderr=err) == 2
    assert 'command_status: invalid' in err.getvalue()


@pytest.mark.parametrize('interactive', [False, True])
def test_native_start_keeps_approval_and_console_size(monkeypatch, interactive):
    events = []
    monkeypatch.delenv('CCB_NO_ATTACH', raising=False)
    monkeypatch.setattr(native, '_stream_is_tty', lambda stream: interactive)
    monkeypatch.setattr(shared_start, '_ensure_project_commands_approved', lambda *a: events.append('approval'))
    monkeypatch.setattr(shared_start, '_ensure_herdr_runtime_evidence', lambda *a: events.append('evidence'))
    monkeypatch.setattr(shared_start, '_terminal_size_for_streams', lambda *a: (100, 30))
    monkeypatch.setattr(native, 'attach_started_project_namespace', lambda *a: events.append('attach'))
    def start(*args, **kwargs):
        assert kwargs == ({'terminal_size': (100, 30)} if interactive else {})
        events.append('start')
    services = SimpleNamespace(start_agents=start, render_start=lambda result: [],
                               write_lines=lambda *a: events.append('render'))
    assert native.handle_start(None, None, StringIO(), services) == 0
    assert events == ['approval', 'evidence', 'start', 'attach' if interactive else 'render']


def test_native_wrapper_keeps_herdr_gate_and_safe_version(monkeypatch, capsys):
    monkeypatch.setattr('stdio_runtime.setup_windows_encoding', lambda: None)
    monkeypatch.setenv('CCB_SKIP_HERDR_CHECK', '1')
    path = Path(__file__).resolve().parents[1] / 'platforms/windows/ccb.py'
    spec = importlib.util.spec_from_file_location('native_wrapper_gate_test', path)
    wrapper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wrapper)
    monkeypatch.setattr(wrapper.ccb, '_source_runtime_allowed', lambda *a: (True, ''))
    monkeypatch.setattr(wrapper.ccb, '_herdr_ok', False)
    monkeypatch.setattr(sys, 'argv', ['ccb', 'doctor'])
    assert wrapper.main() == 1
    assert 'Herdr is not ready' in capsys.readouterr().err
    monkeypatch.setattr(sys, 'argv', ['ccb', '--print-version'])
    assert wrapper.main() == 0
    assert capsys.readouterr().out.strip() == 'v' + wrapper.ccb.VERSION
