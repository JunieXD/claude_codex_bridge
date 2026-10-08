from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


def test_windows_install_keeps_backend_environment_confirmation_before_install_work() -> None:
    text = Path('platforms/windows/installer/install.ps1').read_text(encoding='utf-8-sig')
    install_body = text.split('function Install-Native', 1)[1]

    assert 'function Confirm-BackendEnv' in text
    assert install_body.index('Confirm-BackendEnv') < install_body.index('$pythonCmd = Find-Python')
    assert 'Show-WindowsX64ReleaseSurfaceProjection' in install_body


def test_windows_release_surface_diagnostics_do_not_claim_rmux_packaging_support() -> None:
    text = Path('platforms/windows/installer/install.ps1').read_text(encoding='utf-8-sig').lower()

    release_surface_start = text.index('function show-windowsx64releasesurfaceprojection')
    release_surface_end = text.index('function test-windowsx64releasehostgatevaluepresent', release_surface_start)
    assert release_surface_end > release_surface_start
    release_surface_block = text[release_surface_start:release_surface_end]

    assert 'rmux' not in release_surface_block
    assert 'support_tier' not in release_surface_block


def test_root_windows_installer_is_a_thin_platform_wrapper() -> None:
    text = Path('install.ps1').read_text(encoding='utf-8-sig')

    assert 'platforms\\windows\\installer\\install.ps1' in text
    assert 'function Install-Native' not in text


def test_windows_installer_creates_and_smokes_managed_python_runtime() -> None:
    installer = Path('platforms/windows/installer/install.ps1').read_text(encoding='utf-8-sig')
    requirements = Path('platforms/windows/installer/requirements.txt').read_text(encoding='utf-8')

    assert 'function Install-ManagedPythonRuntime' in installer
    assert '-m venv $venvDir' in installer
    assert '--requirement $requirements' in installer
    assert "import aiohttp, cryptography, watchdog" in installer
    assert 'aiohttp==' in requirements
    assert 'cryptography==' in requirements
    assert 'watchdog>=' in requirements


def test_windows_installer_yes_mode_never_prompts_for_missing_herdr() -> None:
    installer = Path('platforms/windows/installer/install.ps1').read_text(encoding='utf-8-sig')
    herdr_block = installer.split('function Confirm-HerdrReady', 1)[1].split(
        'function Install-Native', 1
    )[0]

    acknowledgement = 'if ($Yes -or $env:CCB_INSTALL_ASSUME_YES -eq "1")'
    assert acknowledgement in herdr_block
    assert herdr_block.index(acknowledgement) < herdr_block.index('Read-Host "继续安装? (y/N)"')
    assert '$reply = [string](Read-Host "继续安装? (y/N)")' in herdr_block


@pytest.mark.skipif(not (shutil.which('pwsh') or shutil.which('powershell')),
                    reason='native PowerShell is required for installer payload execution')
def test_native_payload_install_and_update_preserve_entrypoint_paths(tmp_path: Path) -> None:
    installer = Path('platforms/windows/installer/install.ps1').read_text(encoding='utf-8-sig')
    install = installer.split('function Install-Native {', 1)[1]
    copy = install[install.index('$items = @('):install.index('$pythonExecutable = Install-ManagedPythonRuntime')]
    source = tmp_path / 'source payload'
    target = tmp_path / 'installed prefix'
    payloads = ['ccb.py', 'lib/platforms/windows/herdr/entrypoint.py',
                'platforms/windows/ccb.py', 'platforms/windows/start.ps1',
                'platforms/windows/installer/requirements.txt']
    for path in payloads:
        file = source / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text('first payload', encoding='utf-8')
    literal = lambda path: "'" + str(path).replace("'", "''") + "'"
    script = tmp_path / 'copy.ps1'
    script.write_text("$ErrorActionPreference = 'Stop'\n"
                      f'$repoRoot = {literal(source)}\n$InstallPrefix = {literal(target)}\n'
                      + copy, encoding='utf-8')
    command = [shutil.which('pwsh') or shutil.which('powershell'), '-NoProfile',
               '-NonInteractive', '-File', str(script)]
    for value in ['first payload', 'updated payload']:
        (source / 'platforms/windows/ccb.py').write_text(value, encoding='utf-8')
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        for path in payloads:
            assert (target / path).read_bytes() == (source / path).read_bytes()
        assert not (target / 'platforms/windows/windows').exists()
