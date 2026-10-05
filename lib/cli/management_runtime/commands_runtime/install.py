from __future__ import annotations

from pathlib import Path

from cli.render import render_mobile_serve
from cli.services.mobile import prepare_server_mobile_gateway
from cli.tools_runtime.workbench import print_workbench_status, uninstall_workbench

from ..claude_home_cleanup import cleanup_claude_files
from ..install import run_installer
from ..fork_runtime import is_fork_runtime, update_fork_runtime


def cmd_install(args, *, script_root: Path) -> int:
    del script_root
    target = str(getattr(args, 'target', '') or '').strip().lower()
    if target != 'mobile':
        print("❌ Unsupported install target")
        print("💡 Use: ccb install mobile")
        return 2
    try:
        handle = prepare_server_mobile_gateway(args)
    except Exception as exc:
        print(f"❌ Mobile install failed: {exc}")
        return 1
    for line in render_mobile_serve(handle.summary):
        print(line)
    try:
        handle.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        close = getattr(handle, 'close', None)
        if callable(close):
            close()
    return 0


def cmd_uninstall(args, *, script_root: Path) -> int:
    target = str(getattr(args, 'target', '') or '').strip().lower()
    if target:
        if target != 'rich':
            print(f"❌ Unsupported uninstall target: {target}")
            print("💡 Use: ccb uninstall rich")
            return 2
        result = uninstall_workbench(profile='rich', remove_cache=False)
        print_workbench_status(result)
        return 0 if result.get('status') in {'ok', 'missing'} else 1
    if is_fork_runtime(script_root):
        print('Automatic Fork runtime uninstall is disabled to preserve provider data. Stop projects and remove only the independent runtime and its command links manually.')
        return 1
    cleanup_claude_files()
    return run_installer("uninstall", script_root=script_root)


def cmd_reinstall(_args, *, script_root: Path) -> int:
    if is_fork_runtime(script_root):
        return update_fork_runtime(_args, script_root=script_root, rebuild=True)
    cleanup_claude_files()
    return run_installer("install", script_root=script_root)


__all__ = ['cmd_install', 'cmd_reinstall', 'cmd_uninstall']
