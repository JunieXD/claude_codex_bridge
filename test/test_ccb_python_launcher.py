from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "bin" / "_ccb-python"


def _launcher_cmd(path: Path, *args: str) -> list[str]:
    # bash launchers are plain scripts without an extension; Windows
    # CreateProcess cannot exec them directly, so route through bash there.
    if sys.platform == "win32":
        return ["bash", str(path), *args]
    return [str(path), *args]


def _normalize_path(raw: str) -> str:
    """Normalize a launcher-emitted path for comparison on Windows.

    Git Bash emits MSYS-style paths (e.g. ``/tmp/...``); convert to the native
    Windows form via ``cygpath`` so it matches ``Path()`` on the test side. On
    POSIX the value is used unchanged.
    """
    path = raw.strip()
    if sys.platform == "win32" and path.startswith("/"):
        try:
            out = subprocess.run(
                ["cygpath", "-w", path],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            path = out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return os.path.normcase(os.path.abspath(path))


def _install_launcher(tmp_path: Path) -> Path:
    # Run a copy: a development .venv beside the source checkout would
    # otherwise win as the release-managed interpreter.
    launcher = tmp_path / "install" / "bin" / "_ccb-python"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(LAUNCHER, launcher)
    return launcher


def test_launcher_prefers_install_managed_python_over_stale_inherited_state(
    tmp_path: Path,
) -> None:
    installed_launcher = _install_launcher(tmp_path)
    install_root = installed_launcher.parents[1]

    probe_log = tmp_path / "probe.log"
    managed = install_root / ".venv" / "bin" / "python"
    managed.parent.mkdir(parents=True)
    stale_inherited = tmp_path / "stale-python"
    stale_cached = tmp_path / "cached-python"
    _write_probe(managed, compatible=True)
    _write_probe(stale_inherited, compatible=False)
    _write_probe(stale_cached, compatible=False)
    cache_path = tmp_path / "python-cache"
    cache_path.write_text(f"{stale_cached}\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "CCB_PYTHON": str(stale_inherited),
            "CCB_PYTHON_CACHE": str(cache_path),
            "PROBE_LOG": str(probe_log),
        }
    )

    completed = subprocess.run(
        _launcher_cmd(installed_launcher, "--resolve"),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert _normalize_path(completed.stdout) == _normalize_path(str(managed))
    assert _normalize_path(cache_path.read_text(encoding="utf-8")) == _normalize_path(str(managed))
    probes = probe_log.read_text(encoding="utf-8")
    assert "stale-python:aiohttp" in probes
    assert "python:aiohttp" in probes
    assert "cached-python:aiohttp" not in probes


def test_launcher_skips_higher_python_missing_required_packages(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    probe_log = tmp_path / "probe.log"
    incompatible = fake_bin / "python3.13"
    incompatible_312 = fake_bin / "python3.12"
    incompatible_311 = fake_bin / "python3.11"
    compatible = fake_bin / "python3.10"
    _write_probe(incompatible, compatible=False)
    _write_probe(incompatible_312, compatible=False)
    _write_probe(incompatible_311, compatible=False)
    _write_probe(compatible, compatible=True)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CCB_PYTHON_CACHE": str(tmp_path / "python-cache"),
            "PROBE_LOG": str(probe_log),
        }
    )
    env.pop("CCB_PYTHON", None)

    completed = subprocess.run(
        _launcher_cmd(_install_launcher(tmp_path), "--resolve"),
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert _normalize_path(completed.stdout) == _normalize_path(str(compatible))
    probes = probe_log.read_text(encoding="utf-8")
    assert "python3.13:aiohttp" in probes
    assert "python3.13:cryptography-runtime" in probes


def test_launcher_falls_back_to_python_when_python3_is_store_stub(
    tmp_path: Path,
) -> None:
    # NativeWindows: `python3` resolves to the Windows Store stub (incompatible),
    # while bare `python` is a real interpreter. The launcher must skip the stub
    # and pick `python`.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        _write_probe(fake_bin / name, compatible=False)
    real = fake_bin / "python"
    _write_probe(real, compatible=True)

    probe_log = tmp_path / "probe.log"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CCB_PYTHON_CACHE": str(tmp_path / "python-cache"),
            "PROBE_LOG": str(probe_log),
        }
    )
    env.pop("CCB_PYTHON", None)

    completed = subprocess.run(
        _launcher_cmd(_install_launcher(tmp_path), "--resolve"),
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert _normalize_path(completed.stdout) == _normalize_path(str(real))
    probes = probe_log.read_text(encoding="utf-8")
    assert "python3:aiohttp" in probes  # stub was probed and rejected


def test_launcher_resolves_py_launcher_candidate(tmp_path: Path) -> None:
    # NativeWindows: `python` is a Store stub (incompatible) and only the `py`
    # launcher is real; it must be probed after the python3.* family and win.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        _write_probe(fake_bin / name, compatible=False)
    py_launcher = fake_bin / "py"
    _write_probe(py_launcher, compatible=True)
    _write_probe(fake_bin / "python", compatible=False)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CCB_PYTHON_CACHE": str(tmp_path / "python-cache"),
            "PROBE_LOG": str(tmp_path / "probe.log"),
        }
    )
    env.pop("CCB_PYTHON", None)

    completed = subprocess.run(
        _launcher_cmd(_install_launcher(tmp_path), "--resolve"),
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert _normalize_path(completed.stdout) == _normalize_path(str(py_launcher))


def test_launcher_prefers_windows_managed_venv_over_path_probe(
    tmp_path: Path,
) -> None:
    # NativeWindows managed venv layout: .venv/Scripts/python.exe must win over
    # PATH probing without falling through to the generic `python` candidate.
    installed_launcher = _install_launcher(tmp_path)
    install_root = installed_launcher.parents[1]

    managed = install_root / ".venv" / "Scripts" / "python.exe"
    managed.parent.mkdir(parents=True)
    _write_probe(managed, compatible=True)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    path_python = fake_bin / "python"
    _write_probe(path_python, compatible=True)

    probe_log = tmp_path / "probe.log"
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CCB_PYTHON_CACHE": str(tmp_path / "python-cache"),
            "PROBE_LOG": str(probe_log),
        }
    )
    env.pop("CCB_PYTHON", None)

    completed = subprocess.run(
        _launcher_cmd(installed_launcher, "--resolve"),
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert _normalize_path(completed.stdout) == _normalize_path(str(managed))
    probes = probe_log.read_text(encoding="utf-8")
    assert "python.exe:aiohttp" in probes
    assert "python:aiohttp" not in probes  # PATH probe never reached


def _write_probe(path: Path, *, compatible: bool) -> None:
    result = "0" if compatible else "1"
    path.write_text(
        f"""#!/usr/bin/env bash
payload="$(cat)"
grep -q 'import aiohttp' <<<"$payload" && echo "$(basename "$0"):aiohttp" >>"$PROBE_LOG"
grep -q 'cryptography.hazmat' <<<"$payload" && echo "$(basename "$0"):cryptography-runtime" >>"$PROBE_LOG"
exit {result}
""",
        encoding="utf-8",
    )
    path.chmod(0o755)
