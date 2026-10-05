from pathlib import Path
import os
import re
import shlex
import subprocess


FORK_REPOSITORY = 'JunieXD/claude_codex_bridge'


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ['git', '-C', str(root), *arguments], check=True,
        text=True, capture_output=True, timeout=120,
    ).stdout.strip()


def _is_fork_remote(remote_url: str) -> bool:
    normalized = remote_url.rstrip('/').removesuffix('.git').lower()
    repository = FORK_REPOSITORY.lower()
    return normalized in {
        f'https://github.com/{repository}',
        f'git@github.com:{repository}',
        f'ssh://git@github.com/{repository}',
    }


def _require_clean_source(root: Path) -> None:
    if _git(root, 'status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Source checkout has local changes; commit them before updating.')
    for marker in ['MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply']:
        marker_path = Path(_git(root, 'rev-parse', '--git-path', marker))
        if (root / marker_path).exists():
            raise ValueError('Finish or abort the current Git operation before updating.')


def _source_branch(root: Path) -> str:
    if Path(_git(root, 'rev-parse', '--show-toplevel')).resolve() != root:
        raise ValueError('The CCB source directory must be the Git checkout root.')
    branch = _git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
    if not _is_fork_remote(_git(root, 'remote', 'get-url', 'origin')):
        raise ValueError(f'Origin must point to the maintained Fork: {FORK_REPOSITORY}.')
    return branch


def source_update_status(root: Path) -> dict:
    root = Path(root).resolve()
    branch = _source_branch(root)
    remote_ref = f'refs/remotes/origin/{branch}'
    _git(root, 'fetch', '--no-tags', 'origin', f'refs/heads/{branch}:{remote_ref}')
    ahead, behind = map(int, _git(root, 'rev-list', '--left-right', '--count', f'HEAD...{remote_ref}').split())
    state = 'diverged' if ahead and behind else 'ahead' if ahead else 'behind' if behind else 'current'
    return {
        'state': state, 'ahead': ahead, 'behind': behind, 'branch': branch,
        'repository': FORK_REPOSITORY, 'remote_commit': _git(root, 'rev-parse', remote_ref),
    }


def _source_runtime_processes(root: Path, process_table: str) -> list[int]:
    relative_paths = (
        'ccb', 'ccb.py', 'lib/ccbd/main.py', 'lib/ccbd/keeper_main.py',
        'bin/ccb-agent-sidebar', 'tools/ccb-agent-sidebar/target/release/ccb-agent-sidebar',
    )
    paths = {str(root / name) for name in relative_paths}
    found = []
    for row in process_table.splitlines():
        parts = row.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) == os.getpid():
            continue
        try:
            arguments = shlex.split(parts[1])
        except ValueError:
            arguments = []
        if len(arguments) > 1 and arguments[1] in {'-c', '-lc', '-xc'}:
            continue
        if any(argument in paths for argument in arguments) or any(
            re.search(rf'(?<!\S){re.escape(path)}(?=\s|$)', parts[1]) for path in paths
        ):
            found.append(int(parts[0]))
    return found


def _require_source_runtimes_stopped(root: Path) -> None:
    process_table = subprocess.run(
        ['ps', '-ww', '-axo', 'pid=,args='], check=True,
        text=True, capture_output=True, timeout=10,
    ).stdout
    processes = _source_runtime_processes(root, process_table)
    if processes:
        raise ValueError(
            f'Source runtimes are still running (PIDs: {", ".join(map(str, processes))}). '
            'Finish queued/running work, then exit source CCB projects before updating; '
            'idle runtimes can accept new tasks and must also be stopped.'
            ' After work finishes, run ccb kill inside each source project and retry.'
        )


def update_source_from_fork(args, *, script_root: Path) -> int:
    if getattr(args, 'target', None):
        print('Source updates follow your Fork branch, not official release numbers.')
        return 1
    root = Path(script_root).resolve()
    try:
        branch = _source_branch(root)
        _require_clean_source(root)
        _require_source_runtimes_stopped(root)
        previous_head = _git(root, 'rev-parse', 'HEAD')
        remote_ref = f'refs/remotes/origin/{branch}'
        print(f'Updating source from {FORK_REPOSITORY}, branch {branch} (fast-forward only).')
        _git(root, 'fetch', '--no-tags', 'origin', f'refs/heads/{branch}:{remote_ref}')
        _require_clean_source(root)
        _require_source_runtimes_stopped(root)
        if (_git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD') != branch
                or _git(root, 'rev-parse', 'HEAD') != previous_head):
            raise ValueError('Checkout changed during fetch; update refused.')
        _git(root, 'merge', '--ff-only', '--no-edit', remote_ref)
        current_head = _git(root, 'rev-parse', 'HEAD')
    except (ValueError, OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f'Source update refused: {str(detail or error).strip()[:1000]}')
        print('No installer, reset, stash, or runtime restart was performed. Resolve Git state and retry.')
        return 1
    if current_head == previous_head:
        print('Fork source is already up to date.')
    else:
        print(f'Fork source updated: {previous_head[:12]} -> {current_head[:12]}.')
    print('Global entrypoints were not changed; running agents were not restarted.')
    print('Restart idle source-installed agents when ready to load the updated code.')
    return 0
