"""Windows CLI composition using shared command helpers without modifying them.

The ordering follows cli.entrypoint_runtime; only the final startup dispatch is
owned here. Contract tests protect early commands and shared module identity.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from cli import entrypoint_runtime as shared


def run_native_cli_entrypoint(
    argv: list[str],
    *,
    version: str,
    script_root: Path,
    cwd: Path,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    tokens = list(argv or [])
    shared._log_received_argv(tokens, stderr=stderr)
    if shared._should_print_version(tokens):
        print(f"v{version}", file=stdout)
        return 0
    sidebar_click_result = shared.maybe_handle_sidebar_click_command(tokens, stderr=stderr)
    if sidebar_click_result is not None:
        return sidebar_click_result
    sidebar_resize_sync_result = shared.maybe_handle_sidebar_resize_sync_command(tokens, stderr=stderr)
    if sidebar_resize_sync_result is not None:
        return sidebar_resize_sync_result
    internal_result = shared.maybe_handle_background_update_refresh_command(tokens, script_root=script_root)
    if internal_result is not None:
        return internal_result
    internal_result = shared.maybe_handle_mobile_host_serve_command(tokens, script_root=script_root)
    if internal_result is not None:
        return internal_result
    internal_result = shared.maybe_handle_post_update_command(tokens, script_root=script_root)
    if internal_result is not None:
        return internal_result

    help_result = shared._handle_help(tokens, stdout=stdout)
    if help_result is not None:
        return help_result

    tokens = shared._rewrite_version_alias(tokens)

    removed_result = shared._handle_removed_commands(tokens, stderr=stderr)
    if removed_result is not None:
        return removed_result

    auxiliary_result = shared._dispatch_auxiliary(tokens, script_root=script_root)
    if auxiliary_result is not None:
        return auxiliary_result

    management_result = shared._dispatch_management(tokens, script_root=script_root)
    if management_result is not None:
        return management_result

    rich_result = shared._dispatch_rich(tokens, script_root=script_root, cwd=cwd, stdout=stdout, stderr=stderr)
    if rich_result is not None:
        return rich_result

    tools_result = shared._dispatch_tools(tokens, script_root=script_root, stdout=stdout, stderr=stderr)
    if tools_result is not None:
        return tools_result

    theme_result = shared._dispatch_theme(tokens, stdout=stdout, stderr=stderr)
    if theme_result is not None:
        return theme_result

    roles_result = shared._dispatch_roles(tokens, script_root=script_root, cwd=cwd, stdout=stdout, stderr=stderr)
    if roles_result is not None:
        return roles_result

    auto_rich_result = shared._dispatch_auto_rich_start(tokens, script_root=script_root, cwd=cwd, stdout=stdout, stderr=stderr)
    if auto_rich_result is not None:
        return auto_rich_result

    startup_update_result = shared.maybe_handle_startup_release_update(
        tokens,
        script_root=script_root,
        cwd=cwd,
        stdout=stdout,
        stderr=stderr,
        stdin=sys.stdin,
    )
    if startup_update_result is not None:
        return startup_update_result

    from .entrypoint import _native_phase2

    return _native_phase2(tokens, cwd=cwd, stdout=stdout, stderr=stderr)
