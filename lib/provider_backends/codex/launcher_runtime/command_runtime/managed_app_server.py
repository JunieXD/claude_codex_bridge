from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import shlex
import shutil
import subprocess

from provider_backends.codex.runtime_artifacts import codex_runtime_artifact_layout
from provider_backends.codex.start_cmd_runtime.rewriting import _continuation_subcommand_index


def supports_managed_app_server(provider_start: tuple[str, ...]) -> bool:
    if len(provider_start) != 1:
        return False
    executable = str(provider_start[0] or '').strip()
    if not executable:
        return False
    resolved = shutil.which(executable)
    if not resolved:
        return False
    path = Path(resolved).resolve()
    try:
        stat = path.stat()
    except OSError:
        return False
    return _supports_managed_app_server_executable(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=16)
def _supports_managed_app_server_executable(executable: str, mtime_ns: int, size: int) -> bool:
    del mtime_ns, size
    try:
        version = subprocess.run(
            [executable, '--version'],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
        if version.returncode != 0 or not str(version.stdout or '').strip().startswith('codex-cli '):
            return False
        cli_help = subprocess.run(
            [executable, '--help'],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
        app_server_help = subprocess.run(
            [executable, 'app-server', '--help'],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except Exception:
        return False
    return (
        cli_help.returncode == 0
        and '--remote' in cli_help.stdout
        and app_server_help.returncode == 0
        and '--listen' in app_server_help.stdout
    )


supports_managed_app_server.cache_clear = _supports_managed_app_server_executable.cache_clear


def supports_session_fork(provider_start: tuple[str, ...]) -> bool:
    if len(provider_start) != 1:
        return False
    resolved = shutil.which(str(provider_start[0] or '').strip())
    if not resolved:
        return False
    path = Path(resolved).resolve()
    try:
        stat = path.stat()
    except OSError:
        return False
    return _supports_session_fork_executable(str(path), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=16)
def _supports_session_fork_executable(executable: str, mtime_ns: int, size: int) -> bool:
    del mtime_ns, size
    try:
        result = subprocess.run(
            [executable, 'fork', '--help'],
            capture_output=True,
            text=True,
            timeout=3.0,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and 'Fork a previous interactive session' in str(result.stdout or '')


supports_session_fork.cache_clear = _supports_session_fork_executable.cache_clear


def build_managed_app_server_command(
    codex_args: list[str],
    *,
    runtime_dir: Path,
) -> tuple[str, dict[str, object]]:
    base_args, continuation_mode, resume_id = _split_continuation(codex_args)
    if not base_args:
        raise ValueError('managed Codex app-server requires an executable')
    if continuation_mode == 'fork':
        raise ValueError('managed Codex app-server does not provide verified fork semantics')
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    socket_path = artifacts.app_server_socket
    socket_url = f'unix://{socket_path}'
    # Permissions go to the app-server so `--remote ... resume` accepts the
    # TUI arguments; the local fallback keeps the original flags.
    split = split_permission_overrides(base_args[1:])
    server_config, remote_rest = split if split is not None else ([], base_args[1:])
    remote_args = [base_args[0], '--remote', socket_url, *remote_rest]
    local_args = list(base_args)
    command = _managed_shell_command(
        remote_args=remote_args,
        local_args=local_args,
        socket_path=socket_path,
        remote_marker=artifacts.app_server_remote_marker,
        resume_id=resume_id,
        continuation_mode=continuation_mode,
    )
    executable = base_args[0]
    return command, {
        'codex_app_server_enabled': True,
        'codex_app_server_socket': str(socket_path),
        'codex_app_server_remote_marker': str(artifacts.app_server_remote_marker),
        'codex_app_server_command': [executable, 'app-server', '--listen', socket_url, *server_config],
    }


# `codex --remote ... resume <id>` rejects permission overrides ("Permission
# overrides are not supported when resuming a remote task"). The managed
# app-server owns the threads, so the same policy is applied there as config:
# with codex-cli 0.160 a remote resume reports the app-server's sandbox and
# approval policy. `--dangerously-bypass-hook-trust` is accepted by a remote
# resume and stays on the TUI. `--approve-for-me` has no config equivalent.
_PERMISSION_FLAG_KEYS = {
    '--ask-for-approval': 'approval_policy',
    '-a': 'approval_policy',
    '--sandbox': 'sandbox_mode',
    '-s': 'sandbox_mode',
}
_PERMISSION_CONFIG_KEYS = {'approval_policy', 'sandbox_mode'}


def split_permission_overrides(args: list[str]) -> tuple[list[str], list[str]] | None:
    """Split Codex TUI options into app-server `-c` overrides and the rest.

    Returns None when an override cannot be expressed as app-server config.
    """
    config: list[str] = []
    rest: list[str] = []
    tokens = iter(args)
    for token in tokens:
        name, attached, value = token.partition('=')
        if name in _PERMISSION_FLAG_KEYS:
            value = value if attached else next(tokens, '')
            config += ['-c', f'{_PERMISSION_FLAG_KEYS[name]}="{value}"']
        elif name == '--dangerously-bypass-approvals-and-sandbox':
            config += ['-c', 'approval_policy="never"', '-c', 'sandbox_mode="danger-full-access"']
        elif name == '--approve-for-me':
            return None
        elif name in {'-c', '--config'}:
            item = value if attached else next(tokens, '')
            if item.partition('=')[0].strip().lower() in _PERMISSION_CONFIG_KEYS:
                config += ['-c', item]
            else:
                rest += [token] if attached else [token, item]
        else:
            rest.append(token)
    return config, rest


def _split_continuation(codex_args: list[str]) -> tuple[list[str], str, str]:
    index = _continuation_subcommand_index(codex_args, 0)
    if index is None:
        return list(codex_args), '', ''
    token = codex_args[index]
    if index + 1 >= len(codex_args) or index + 2 != len(codex_args):
        raise ValueError(f'managed Codex {token} requires one terminal session id')
    return list(codex_args[:index]), token, str(codex_args[index + 1])


def _split_resume(codex_args: list[str]) -> tuple[list[str], str]:
    base_args, mode, session_id = _split_continuation(codex_args)
    if mode == 'fork':
        return list(codex_args), ''
    return base_args, session_id


def _managed_shell_command(
    *,
    remote_args: list[str],
    local_args: list[str],
    socket_path: Path,
    remote_marker: Path,
    resume_id: str,
    continuation_mode: str = 'resume',
) -> str:
    quoted_socket = shlex.quote(str(socket_path))
    quoted_marker = shlex.quote(str(remote_marker))
    quoted_resume = shlex.quote(resume_id)
    quoted_ref = shlex.quote(str(remote_marker) + '.wait-ref')
    remote = ' '.join(shlex.quote(str(part)) for part in remote_args)
    local = ' '.join(shlex.quote(str(part)) for part in local_args)
    mode = continuation_mode if continuation_mode in {'resume', 'fork'} else 'resume'
    return '; '.join(
        (
            f'export CCB_CODEX_MANAGED_REMOTE=1 CCB_CODEX_RESUME_ID={quoted_resume}',
            f'rm -f {quoted_marker}',
            # The wait reference is stamped before the first existence check
            # so a leftover socket node from a previous generation (created
            # before this pane started) cannot satisfy the wait: a stale node
            # is never newer than the reference. A socket bound by the
            # current generation after this pane started is newer and is
            # accepted; when `find -newer` is unavailable the plain `-S`
            # existence check still applies, and the final `exec ... --remote`
            # remains the real connection arbiter (#345).
            f': > {quoted_ref}',
            '_ccb_codex_wait=0',
            (
                f'while [ "$_ccb_codex_wait" -lt 100 ]; do '
                f'if [ -S {quoted_socket} ] && {{ '
                f'[ -n "$(find {quoted_socket} -newer {quoted_ref} 2>/dev/null)" ] || [ ! -x "$(command -v find)" ]; '
                '}; then break; fi; '
                'sleep 0.05; _ccb_codex_wait=$((_ccb_codex_wait + 1)); done'
            ),
            (
                f'if [ -S {quoted_socket} ] && {{ '
                f'[ -n "$(find {quoted_socket} -newer {quoted_ref} 2>/dev/null)" ] || [ ! -x "$(command -v find)" ]; '
                '}; then '
                f"printf '%s\\n' {quoted_socket} > {quoted_marker}; "
                f'if [ -n "$CCB_CODEX_RESUME_ID" ]; then exec {remote} {mode} "$CCB_CODEX_RESUME_ID"; '
                f'else exec {remote}; fi; fi'
            ),
            f'rm -f {quoted_ref}',
            (
                f'if [ -n "$CCB_CODEX_RESUME_ID" ]; then exec {local} {mode} "$CCB_CODEX_RESUME_ID"; '
                f'else exec {local}; fi'
            ),
        )
    )

__all__ = [
    'build_managed_app_server_command',
    'split_permission_overrides',
    'supports_managed_app_server',
    'supports_session_fork',
]
