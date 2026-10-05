from pathlib import Path
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


def update_source_from_fork(args, *, script_root: Path) -> int:
    if getattr(args, 'target', None):
        print('Source updates follow your Fork branch, not official release numbers.')
        return 1
    root = Path(script_root).resolve()
    try:
        if Path(_git(root, 'rev-parse', '--show-toplevel')).resolve() != root:
            raise ValueError('The CCB source directory must be the Git checkout root.')
        branch = _git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD')
        if not _is_fork_remote(_git(root, 'remote', 'get-url', 'origin')):
            raise ValueError(f'Origin must point to the maintained Fork: {FORK_REPOSITORY}.')
        _require_clean_source(root)
        previous_head = _git(root, 'rev-parse', 'HEAD')
        remote_ref = f'refs/remotes/origin/{branch}'
        print(f'Updating source from {FORK_REPOSITORY}, branch {branch} (fast-forward only).')
        _git(root, 'fetch', '--no-tags', 'origin', f'refs/heads/{branch}:{remote_ref}')
        _require_clean_source(root)
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
