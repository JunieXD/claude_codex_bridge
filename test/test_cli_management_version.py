from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from cli.management_runtime.commands_runtime import version as version_runtime


def test_cmd_version_for_source_install_uses_fork_update(monkeypatch, tmp_path: Path, capsys) -> None:
    source_dir = tmp_path / "source-install"
    source_dir.mkdir()
    (source_dir / ".git").mkdir()
    monkeypatch.setattr(version_runtime, "find_install_dir", lambda _script_root: source_dir)
    monkeypatch.setattr(
        version_runtime,
        "get_version_info",
        lambda _install_dir: {
            "version": "6.0.12",
            "commit": "abc1234",
            "date": "2026-04-24",
            "install_mode": "source",
            "source_kind": "source",
            "channel": "dev",
        },
    )
    monkeypatch.setattr(
        version_runtime,
        "source_update_status",
        lambda root: {'state': 'behind', 'ahead': 0, 'behind': 1, 'repository': 'JunieXD/claude_codex_bridge', 'branch': 'main'},
    )

    code = version_runtime.cmd_version(SimpleNamespace(), script_root=tmp_path / "bin-root")

    assert code == 0
    captured = capsys.readouterr()
    assert "Source update available" in captured.out
    assert "JunieXD/claude_codex_bridge" in captured.out
    assert "run: ccb update" in captured.out
    assert "stable release" not in captured.out


def test_source_version_ahead_does_not_advertise_an_update(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(version_runtime, 'source_update_status', lambda root: {
        'state': 'ahead', 'ahead': 3, 'behind': 0, 'repository': 'JunieXD/claude_codex_bridge', 'branch': 'main',
    })
    version_runtime._print_source_update_status(tmp_path)
    output = capsys.readouterr().out
    assert '3 commit(s) ahead' in output
    assert 'update available' not in output


def test_source_version_error_never_suggests_official_install(monkeypatch, tmp_path, capsys):
    def failure(root):
        raise ValueError('wrong origin')
    monkeypatch.setattr(version_runtime, 'source_update_status', failure)
    version_runtime._print_source_update_status(tmp_path)
    output = capsys.readouterr().out
    assert 'wrong origin' in output
    assert 'Run: ccb update' not in output
    assert './install.sh' not in output


def test_cmd_version_for_release_install_still_suggests_ccb_update(monkeypatch, tmp_path: Path, capsys) -> None:
    install_dir = tmp_path / "release-install"
    install_dir.mkdir()
    monkeypatch.setattr(version_runtime, "find_install_dir", lambda _script_root: install_dir)
    monkeypatch.setattr(
        version_runtime,
        "get_version_info",
        lambda _install_dir: {
            "version": "6.0.11",
            "install_mode": "release",
            "source_kind": "release",
            "channel": "stable",
        },
    )
    monkeypatch.setattr(version_runtime, "get_available_versions", lambda: ["6.0.11", "6.0.12"])

    code = version_runtime.cmd_version(SimpleNamespace(), script_root=tmp_path / "bin-root")

    assert code == 0
    captured = capsys.readouterr()
    assert "Release update available: v6.0.12" in captured.out
    assert "Run: ccb update" in captured.out
