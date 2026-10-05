from __future__ import annotations

import json
import os
from pathlib import Path
import sys

FLAG = 'CCB_CLAUDE_CACHE_KEEPALIVE'
LAUNCH_STATUS_FILE = 'cache-keepalive-launch.json'


def flag_enabled(value) -> bool:
    return str(value).strip().lower() in {'1', 'true'}


def keepalive_requested(extra_env=None) -> bool:
    return flag_enabled((extra_env or {}).get(FLAG, os.environ.get(FLAG, '0')))


def configure_cache_keepalive(cmd_parts: list[str], managed_env: dict[str, str], *, extra_env=None, startup_args=()) -> str:
    """Load the mod when requested; return the reason recorded for this launch."""
    if not keepalive_requested(extra_env):
        return 'disabled'
    if any(str(argument).split('=', 1)[0] in {'--bare', '--safe-mode'} for argument in [*cmd_parts, *startup_args]):
        return 'bare_or_safe_mode'
    hooks = (extra_env or {}).get('CLAUDE_CODE_ENABLE_FUNCTION_HOOKS', os.environ.get('CLAUDE_CODE_ENABLE_FUNCTION_HOOKS'))
    if hooks is not None and str(hooks).lower() in {'0', 'false'}:
        return 'hooks_disabled'
    root = Path(__file__).resolve().parents[3]
    plugin = root / 'config' / 'claude-cache-keepalive'
    bridge = root / 'bin' / 'ccb-claude-cache.py'
    if not plugin.is_dir() or not bridge.is_file():
        return 'plugin_missing'
    cmd_parts.extend(['--plugin-dir', str(plugin)])
    managed_env['CCB_CLAUDE_CACHE_BRIDGE'] = json.dumps([sys.executable, str(bridge)])
    managed_env['CLAUDE_CODE_ENABLE_FUNCTION_HOOKS'] = '1'
    return 'enabled'


def write_launch_status(runtime_dir: Path, reason: str) -> None:
    """Record the daemon-side decision so the CLI can explain a silent no-op."""
    try:
        path = Path(runtime_dir) / LAUNCH_STATUS_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'reason': reason}), encoding='utf-8')
    except OSError:
        pass


def read_launch_status(runtime_dir: Path) -> str | None:
    try:
        value = json.loads((Path(runtime_dir) / LAUNCH_STATUS_FILE).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    reason = value.get('reason') if isinstance(value, dict) else None
    return reason if isinstance(reason, str) else None


def launch_warnings(agents: dict[str, Path], *, shell_env=None) -> list[str]:
    """Warn when the user asked for keepalive but a Claude launch did not load it."""
    shell_requested = flag_enabled((os.environ if shell_env is None else shell_env).get(FLAG, '0'))
    warnings = []
    for name, runtime_dir in sorted(agents.items()):
        reason = read_launch_status(runtime_dir)
        if reason is None or reason == 'enabled':
            continue
        if reason == 'disabled':
            if shell_requested:
                warnings.append(
                    f'{FLAG}=1 in this shell, but ccbd launched Claude agent {name!r} without it, '
                    'so the cache keepalive mod is not loaded. Set it under '
                    f'[agents.{name}.env] in .ccb/ccb.config, or restart this project\'s ccbd '
                    'from a shell that exports it, then restart the agent.'
                )
            continue
        warnings.append(f'Cache keepalive was requested for Claude agent {name!r} but not loaded: {reason}.')
    return warnings
