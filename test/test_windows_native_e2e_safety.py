import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def e2e():
    path = Path(__file__).resolve().parents[1] / 'platforms/windows/tools/e2e_local.py'
    spec = importlib.util.spec_from_file_location('native_e2e_safety', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('existing', ['notes.txt', '.ccb/state.json', '.ccb/ccb.config'])
def test_e2e_refuses_unmarked_nonempty_project_without_writes(e2e, tmp_path, existing):
    project = tmp_path / 'project'
    original = project / existing
    original.parent.mkdir(parents=True)
    original.write_text('preserve me', encoding='utf-8')
    before = sorted(str(p.relative_to(project)) for p in project.rglob('*'))
    with pytest.raises(ValueError, match='not marked'):
        e2e.prepare_project(project)
    assert original.read_text(encoding='utf-8') == 'preserve me'
    assert sorted(str(p.relative_to(project)) for p in project.rglob('*')) == before


def test_e2e_prepares_new_project_and_preserves_marked_configuration(e2e, tmp_path):
    project = tmp_path / 'project'
    e2e.prepare_project(project)
    config = project / '.ccb/ccb.config'
    assert config.is_file() and (project / '.ccb-native-e2e').is_file()
    config.write_text('custom configuration', encoding='utf-8')
    e2e.prepare_project(project)
    assert config.read_text(encoding='utf-8') == 'custom configuration'


def test_runner_cannot_bypass_disposable_marker(e2e, tmp_path):
    with pytest.raises(ValueError, match='marked disposable'):
        e2e.Runner(tmp_path, tmp_path / 'output', {})
    assert not (tmp_path / 'output').exists()


def test_failure_checks_use_owned_temp_dir_and_clean_on_exception(e2e, tmp_path, monkeypatch):
    original = tmp_path / 'invalid-config-e2e/.ccb/ccb.config'
    original.parent.mkdir(parents=True)
    original.write_text('preserve me', encoding='utf-8')
    project = tmp_path / 'project'
    e2e.prepare_project(project)
    runner = e2e.Runner(project, tmp_path / 'output', {'CCB_HERDR_EXE': 'test-herdr'})
    directories = []
    stopped = []
    monkeypatch.setattr(e2e.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    def fail(command, **kwargs):
        if command[0] == 'test-herdr':
            stopped.append(command)
            return SimpleNamespace(returncode=0)
        directory = kwargs['cwd']
        assert directory.is_relative_to(project.resolve())
        assert (directory / '.ccb/ccb.config').read_text() == 'version = [broken TOML'
        directories.append(directory)
        raise RuntimeError('simulated child error')
    monkeypatch.setattr(e2e.subprocess, 'run', fail)
    with pytest.raises(RuntimeError, match='simulated child error'):
        runner.failure_checks()
    assert directories and all(not d.exists() for d in directories)
    from storage.paths import PathLayout
    assert stopped == [['test-herdr', '--session', PathLayout(directories[0]).ccbd_tmux_session_name, 'server', 'stop']]
    assert original.read_text(encoding='utf-8') == 'preserve me'


def test_ui_check_closes_its_gui_on_capture_error(e2e, tmp_path, monkeypatch):
    project = tmp_path / 'project'
    e2e.prepare_project(project)
    runner = e2e.Runner(project, tmp_path / 'output', {})
    cleanup = []
    gui = SimpleNamespace(pid=123, terminate=lambda: cleanup.append('terminate'),
                          wait=lambda **kw: cleanup.append('wait'))
    monkeypatch.setattr(e2e.subprocess, 'Popen', lambda *a, **kw: gui)
    monkeypatch.setattr(e2e.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    def fail(*args, **kwargs):
        raise RuntimeError('capture unavailable')
    monkeypatch.setattr(runner, 'wez', fail)
    with pytest.raises(RuntimeError, match='capture unavailable'):
        runner.ui_checks(tmp_path / 'wezterm.exe')
    assert cleanup == ['terminate', 'wait']
