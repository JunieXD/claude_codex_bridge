from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

from .fork_runtime import MANIFEST_NAME, SOURCE_KIND, fork_runtime_lock, load_fork_runtime, require_runtime_stopped
from .install import safe_extract_tar
from .source_update import FORK_REPOSITORY, _git, _require_clean_source, _require_source_runtimes_stopped, _source_branch


RUNTIME_PATHS = (
    'ccb', 'ccb.py', 'install.sh', 'VERSION', 'package.json', 'LICENSE',
    'lib', 'bin', 'config', 'platforms', 'inherit_skills', 'useful_tools',
    'mcp', 'scripts', 'deploy', 'tools/codex-reconnect', 'assets/config_ui', 'mobile',
)
NATIVE_HELPERS = (
    ('tools/ccb-agent-sidebar', 'ccb-agent-sidebar'),
    ('tools/ccb-rs-helper', 'ccb-rs-helper'),
    ('rust', 'ccb-runtime-accelerator'),
)
ENTRYPOINTS = (
    'ccb', 'bin/_ccb-python', 'bin/ask', 'bin/autonew', 'bin/ctx-transfer',
    'bin/codex-reconnect', 'bin/ccb-provider-activity-hook',
    'bin/ccb-agent-sidebar', 'bin/ccb-rs-helper', 'bin/ccb-runtime-accelerator',
    'bin/build-ccb-agent-sidebar', 'bin/build-ccb-rs-helper', 'bin/build-ccb-runtime-accelerator',
    'config/ccb-status.sh', 'config/ccb-border.sh', 'config/ccb-git.sh',
    'config/ccb-tmux-on.sh', 'config/ccb-tmux-off.sh',
)


def _export_runtime(source: Path, destination: Path, commit: str) -> None:
    paths = [name for name in RUNTIME_PATHS if (source / name).exists()]
    result = subprocess.run(
        ['git', '-C', str(source), 'archive', '--format=tar', commit, *paths],
        check=True, capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        safe_extract_tar(archive, destination)


def _build_native_helpers(source: Path, destination: Path) -> None:
    for relative, name in NATIVE_HELPERS:
        crate = source / relative
        command = ['cargo', 'build', '--release', '--locked', '--manifest-path', str(crate / 'Cargo.toml')]
        if name == 'ccb-runtime-accelerator':
            command.extend(['-p', name])
        environment = dict(os.environ)
        environment['CARGO_TARGET_DIR'] = str(crate / 'target')
        subprocess.run(command, cwd=source, env=environment, check=True)
        binary = crate / 'target/release' / name
        shutil.copy2(binary, destination / 'bin' / name)


def _package_build_entrypoints(destination: Path, final_root: Path) -> None:
    command = shlex.quote(str(final_root / 'ccb'))
    for _, name in NATIVE_HELPERS:
        path = destination / 'bin' / f'build-{name}'
        path.write_text(f'#!/bin/sh\nexec {command} reinstall "$@"\n')
        path.chmod(0o755)


def _copy_python_environment(python: str, destination: Path, final_root: Path) -> None:
    probe = subprocess.run(
        [python, '-I', '-c', 'import json,sys,sysconfig; print(json.dumps([sys._base_executable,sysconfig.get_path("purelib")]))'],
        check=True, capture_output=True, text=True,
    )
    base_python, source_packages = json.loads(probe.stdout)
    environment_root = destination / '.venv'
    subprocess.run([base_python, '-I', '-m', 'venv', '--without-pip', '--copies', str(environment_root)], check=True)
    installed_python = environment_root / 'bin/python'
    target_probe = subprocess.run(
        [str(installed_python), '-I', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
        check=True, capture_output=True, text=True,
    )
    target_packages = Path(target_probe.stdout.strip())
    for path in Path(source_packages).glob('*.pth'):
        for line in path.read_text().splitlines():
            if line.strip() and not line.startswith(('import ', '#')) and Path(line.strip()).is_absolute():
                raise ValueError(f'Python environment contains a non-portable .pth file: {path.name}')
    shutil.copytree(source_packages, target_packages, dirs_exist_ok=True, symlinks=False,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for path in [environment_root / 'pyvenv.cfg', *list((environment_root / 'bin').glob('activate*'))]:
        if path.is_file():
            path.write_text(path.read_text().replace(str(destination), str(final_root)))


def _clean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in ('CCB_PYTHON', 'CCB_PYTHON_CACHE', 'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV',
                 'CCB_INSTALL_KIND', 'CCB_INSTALLED_SOURCE_ROOT', 'CCB_SOURCE_ROOT', 'CODEX_INSTALL_PREFIX'):
        environment.pop(name, None)
    return environment


def _verify_runtime(root: Path) -> None:
    home = root / '.installation-smoke-home'
    home.mkdir()
    environment = _clean_environment()
    environment.update(HOME=str(home), CCB_SKIP_STARTUP_UPDATE_CHECK='1')
    try:
        for relative, arguments in (
            ('ccb', ['--print-version']), ('bin/ask', ['--help']),
            ('bin/codex-reconnect', ['--help']), ('bin/ccb-rs-helper', ['--version']),
            ('bin/ccb-runtime-accelerator', ['--help']),
        ):
            subprocess.run([str(root / relative), *arguments], cwd=root, env=environment,
                           check=True, capture_output=True, timeout=30)
        subprocess.run(
            [str(root / '.venv/bin/python'), '-I', '-c',
             'import aiohttp,watchdog; from cryptography.hazmat.primitives.asymmetric import ed25519,x25519; from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305'],
            check=True, capture_output=True, env=environment, timeout=30,
        )
        sidebar = subprocess.run(
            [str(root / 'bin/ccb-agent-sidebar'), '--help'],
            env=environment, capture_output=True, text=True, timeout=30,
        )
        if sidebar.returncode != 2 or 'usage: ccb-agent-sidebar' not in sidebar.stderr:
            raise ValueError('Packaged sidebar executable failed its smoke check.')
        for path in root.rglob('*'):
            if path.is_symlink() and not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'Runtime contains an external symlink: {path.relative_to(root)}')
    finally:
        shutil.rmtree(home)


def _configure_entrypoints(root: Path, bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    previous = {}
    for relative in ENTRYPOINTS:
        target = root / relative
        entry = bin_dir / target.name
        if not target.is_file() or not os.access(target, os.X_OK):
            raise ValueError(f'Missing runtime entrypoint: {relative}')
        if entry.exists() and not entry.is_symlink():
            raise ValueError(f'Refusing to overwrite a regular executable: {entry}')
        previous[entry] = os.readlink(entry) if entry.is_symlink() else None
    changed = []
    try:
        for relative in ENTRYPOINTS:
            target = root / relative
            entry = bin_dir / target.name
            _replace_link(entry, str(target))
            changed.append(entry)
    except BaseException:
        for entry in reversed(changed):
            if previous[entry] is None:
                entry.unlink(missing_ok=True)
            else:
                _replace_link(entry, previous[entry])
        raise


def _replace_link(entry: Path, target: str) -> None:
    temporary = entry.with_name(f'.{entry.name}.fork-{os.getpid()}')
    try:
        temporary.symlink_to(target)
        os.replace(temporary, entry)
    finally:
        temporary.unlink(missing_ok=True)


def _write_metadata(source: Path, destination: Path, root: Path, bin_dir: Path, *, branch: str, commit: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    version = str(json.loads((destination / 'package.json').read_text())['version'])
    manifest = {
        'schema_version': 1, 'repository': FORK_REPOSITORY, 'branch': branch,
        'commit': commit, 'source_root': str(source), 'installation_root': str(root),
        'bin_dir': str(bin_dir), 'installed_at': now,
    }
    build_info = {
        'version': version, 'commit': commit, 'date': _git(source, 'log', '-1', '--format=%cs'),
        'build_time': now, 'installed_at': now, 'platform': platform.system().lower(),
        'arch': platform.machine(), 'channel': 'fork', 'source_kind': SOURCE_KIND,
        'install_mode': 'release',
    }
    for name, payload in ((MANIFEST_NAME, manifest), ('BUILD_INFO.json', build_info)):
        (destination / name).write_text(json.dumps(payload, indent=2) + '\n')
    (destination / 'VERSION').write_text(version + '\n')


def install_fork_runtime(source_root: Path, runtime_root: Path, bin_dir: Path, *, python: str | None = None, lock_held: bool = False) -> Path:
    if platform.system() not in {'Darwin', 'Linux'}:
        raise ValueError('Separated Fork runtimes are supported on macOS and Linux.')
    source = Path(source_root).resolve()
    root = Path(runtime_root).expanduser().resolve()
    bin_dir = Path(bin_dir).expanduser().resolve()
    if source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise ValueError('Runtime and development checkout must be separate directories.')
    branch = _source_branch(source)
    _require_clean_source(source)
    commit = _git(source, 'rev-parse', 'HEAD')
    if root.exists():
        load_fork_runtime(root)
    previous = root.with_name(f'.{root.name}.previous')
    if previous.exists():
        if not (previous / MANIFEST_NAME).is_file():
            raise ValueError('The rollback directory is not owned by the Fork installer.')
        previous_manifest = json.loads((previous / MANIFEST_NAME).read_text())
        if previous_manifest.get('installation_root') != str(root) or previous_manifest.get('repository') != FORK_REPOSITORY:
            raise ValueError('The rollback directory belongs to another installation.')
    root.parent.mkdir(parents=True, exist_ok=True)
    guard = nullcontext() if lock_held else fork_runtime_lock(root, exclusive=True)
    with guard:
        require_runtime_stopped(root)
        _require_source_runtimes_stopped(source)
        stage = Path(tempfile.mkdtemp(prefix=f'.{root.name}.staging-', dir=root.parent))
        moved_previous = False
        installed = False
        try:
            _export_runtime(source, stage, commit)
            _build_native_helpers(source, stage)
            _package_build_entrypoints(stage, root)
            _copy_python_environment(python or sys.executable, stage, root)
            _write_metadata(source, stage, root, bin_dir, branch=branch, commit=commit)
            _verify_runtime(stage)
            _require_clean_source(source)
            if _source_branch(source) != branch or _git(source, 'rev-parse', 'HEAD') != commit:
                raise ValueError('Development checkout changed during the build; installation was not switched.')
            require_runtime_stopped(root)
            _require_source_runtimes_stopped(source)
            if root.exists():
                if previous.exists():
                    shutil.rmtree(previous)
                root.rename(previous)
                moved_previous = True
            stage.rename(root)
            installed = True
            _configure_entrypoints(root, bin_dir)
        except BaseException:
            if installed:
                shutil.rmtree(root)
            if moved_previous:
                previous.rename(root)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    print(f'Installed independent Fork runtime: {root}')
    print(f'Development checkout and build caches stay at: {source}')
    print('User authentication, settings and project histories were not replaced.')
    return root
