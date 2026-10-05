from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def configure_cache_keepalive(cmd_parts: list[str], managed_env: dict[str, str], *, extra_env=None, startup_args=()) -> None:
    value = (extra_env or {}).get('CCB_CLAUDE_CACHE_KEEPALIVE', os.environ.get('CCB_CLAUDE_CACHE_KEEPALIVE', '0'))
    if str(value).lower() not in {'1', 'true'}:
        return
    if any(str(argument).split('=', 1)[0] in {'--bare', '--safe-mode'} for argument in [*cmd_parts, *startup_args]):
        return
    hooks = (extra_env or {}).get('CLAUDE_CODE_ENABLE_FUNCTION_HOOKS', os.environ.get('CLAUDE_CODE_ENABLE_FUNCTION_HOOKS'))
    if hooks is not None and str(hooks).lower() in {'0', 'false'}:
        return
    root = Path(__file__).resolve().parents[3]
    plugin = root / 'config' / 'claude-cache-keepalive'
    bridge = root / 'bin' / 'ccb-claude-cache.py'
    if not plugin.is_dir() or not bridge.is_file():
        return
    cmd_parts.extend(['--plugin-dir', str(plugin)])
    managed_env['CCB_CLAUDE_CACHE_BRIDGE'] = json.dumps([sys.executable, str(bridge)])
    managed_env['CLAUDE_CODE_ENABLE_FUNCTION_HOOKS'] = '1'
