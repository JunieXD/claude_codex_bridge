from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from .source_update import FORK_REPOSITORY, _git, _require_source_runtimes_stopped, _source_branch


MANIFEST_NAME = 'FORK_RUNTIME.json'
SOURCE_KIND = 'fork-runtime'


def is_fork_runtime(root: Path) -> bool:
    if (Path(root) / MANIFEST_NAME).exists():
        return True
    try:
        payload = json.loads((Path(root) / 'BUILD_INFO.json').read_text())
    except (OSError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get('source_kind') == SOURCE_KIND


def load_fork_runtime(root: Path) -> dict:
    root = Path(root).resolve()
    try:
        payload = json.loads((root / MANIFEST_NAME).read_text())
    except (OSError, ValueError) as error:
        raise ValueError('Fork runtime metadata is missing or invalid; official updates are disabled.') from error
    if not isinstance(payload, dict) or payload.get('schema_version') != 1:
        raise ValueError('Unsupported Fork runtime metadata.')
    if payload.get('repository') != FORK_REPOSITORY:
        raise ValueError('Fork runtime repository does not match the maintained Fork.')
    for name in ('installation_root', 'source_root', 'bin_dir'):
        value = payload.get(name)
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError(f'Invalid Fork runtime {name}.')
    if Path(payload['installation_root']).resolve() != root:
        raise ValueError('Fork runtime installation path does not match its metadata.')
    branch = payload.get('branch')
    if not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', branch) or '..' in branch:
        raise ValueError('Invalid Fork runtime branch.')
    if not re.fullmatch(r'[0-9a-f]{40,64}', str(payload.get('commit') or '')):
        raise ValueError('Invalid Fork runtime commit.')
    return payload


def runtime_lock_path(root: Path) -> Path:
    root = Path(root).resolve()
    return root.parent / f'.{root.name}.install.lock'


@contextmanager
def fork_runtime_lock(root: Path, *, exclusive: bool):
    import fcntl

    path = runtime_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Fork runtime is starting or being updated; wait for it to finish and retry.') from error
        try:
            yield handle.fileno()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def fork_runtime_start_guard(root: Path):
    if not is_fork_runtime(root):
        yield
        return
    load_fork_runtime(root)
    with fork_runtime_lock(root, exclusive=False):
        yield


def require_runtime_stopped(root: Path) -> None:
    _require_source_runtimes_stopped(root)


def fork_runtime_update_status(root: Path) -> dict:
    manifest = load_fork_runtime(root)
    reference = f'refs/heads/{manifest["branch"]}'
    result = subprocess.run(
        ['git', 'ls-remote', '--refs', f'https://github.com/{FORK_REPOSITORY}.git', reference],
        check=True, text=True, capture_output=True, timeout=30,
    )
    records = [line.split() for line in result.stdout.splitlines()]
    commits = [parts[0] for parts in records if len(parts) == 2 and parts[1] == reference]
    if len(commits) != 1 or not re.fullmatch(r'[0-9a-f]{40,64}', commits[0]):
        raise ValueError('Could not find the configured Fork branch.')
    return {
        'repository': FORK_REPOSITORY, 'branch': manifest['branch'],
        'state': 'current' if commits[0] == manifest['commit'] else 'different',
        'remote_commit': commits[0], 'installed_commit': manifest['commit'],
    }


def update_fork_runtime(args, *, script_root: Path, rebuild: bool = False) -> int:
    if getattr(args, 'target', None):
        print('Fork runtime updates follow your Fork branch, not official release numbers.')
        return 1
    try:
        manifest = load_fork_runtime(script_root)
        root = Path(manifest['installation_root'])
        source = Path(manifest['source_root'])
        if not (source / '.git').exists():
            raise ValueError(f'Development checkout is unavailable: {source}. Connect the external disk to update; the installed runtime is unchanged.')
        if _source_branch(source) != manifest['branch']:
            raise ValueError('Development checkout is on another branch; switch back before updating.')
        with fork_runtime_lock(root, exclusive=True) as lock_descriptor:
            require_runtime_stopped(root)
            from .source_update import update_source_from_fork

            if update_source_from_fork(args, script_root=source) != 0:
                return 1
            commit = _git(source, 'rev-parse', 'HEAD')
            if commit == manifest['commit'] and not rebuild:
                print('Fork runtime is already up to date; no files or settings were changed.')
                return 0
            require_runtime_stopped(root)
            environment = dict(os.environ)
            environment['CCB_FORK_INSTALL_LOCK_FD'] = str(lock_descriptor)
            source_python = source / '.venv/bin/python'
            code = subprocess.run(
                [sys.executable, str(source / 'scripts/install_fork_runtime.py'),
                 '--runtime-root', str(root), '--bin-dir', manifest['bin_dir'],
                 '--python', str(source_python) if source_python.is_file() else sys.executable, '--lock-held'],
                cwd=source, env=environment, check=False, pass_fds=(lock_descriptor,),
            ).returncode
            return code
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f'Fork runtime update refused: {str(detail or error).strip()[:1000]}')
        print('No official release, npm install, provider update, or agent restart was performed.')
        return 1
