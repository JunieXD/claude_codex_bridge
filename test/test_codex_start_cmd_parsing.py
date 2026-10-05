from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.models import RestoreMode

from provider_backends.codex.start_cmd_runtime.parsing import (
    extract_resume_session_id,
    looks_like_bare_resume_cmd,
)
from provider_backends.codex.start_cmd_runtime.rewriting import (
    build_resume_start_cmd,
    rewrite_codex_segment,
    strip_resume_from_codex_segment,
    strip_resume_start_cmd,
)


def test_extract_resume_session_id_prefers_regex_match() -> None:
    command = 'export X=1; codex -c disable_paste_burst=true resume sess-123'

    assert extract_resume_session_id(command) == 'sess-123'


def test_extract_resume_session_id_falls_back_to_token_scan() -> None:
    command = '/usr/local/bin/codex resume sess-456'

    assert extract_resume_session_id(command) == 'sess-456'


def test_extract_resume_session_id_rejects_invalid_shell_syntax() -> None:
    command = 'codex "unterminated'

    assert extract_resume_session_id(command) is None


def test_extract_resume_session_id_ignores_codex_paths_in_environment_assignments() -> None:
    command = (
        'export CCB_CODEX_RUNTIME_DIR=/tmp/provider-runtime/codex '
        'CCB_CALLER_PROJECT_ID=project-1; '
        'codex -c disable_paste_burst=true resume sess-exact'
    )

    assert extract_resume_session_id(command) == 'sess-exact'


def test_looks_like_bare_resume_cmd_accepts_simple_resume() -> None:
    assert looks_like_bare_resume_cmd('/usr/local/bin/codex resume sess-789') is True


def test_looks_like_bare_resume_cmd_rejects_shell_wrapped_command() -> None:
    assert looks_like_bare_resume_cmd('export CODEX_HOME=/tmp; codex resume sess-789') is False


def test_strip_resume_start_cmd_removes_resume_suffix_from_shell_wrapped_command() -> None:
    command = 'export CODEX_HOME=/tmp/home CODEX_SESSION_ROOT=/tmp/home/sessions; codex -m gpt-5.4 resume sess-789'

    assert strip_resume_start_cmd(command) == (
        'export CODEX_HOME=/tmp/home CODEX_SESSION_ROOT=/tmp/home/sessions; '
        'codex -m gpt-5.4'
    )


def test_rewrite_codex_segment_replaces_fork_continuation_with_resume() -> None:
    rewritten = rewrite_codex_segment(
        'codex fork 00000000-0000-0000-0000-000000000001',
        '00000000-0000-0000-0000-000000000002',
    )

    assert rewritten == 'codex resume 00000000-0000-0000-0000-000000000002'


@pytest.mark.parametrize('option', ['--profile fork', '-p fork', '--profile=fork',
                                  '-pfork', '--model resume', '-c fork'])
def test_rewrite_preserves_continuation_words_in_option_values(option):
    base = f'codex {option} --model test-model'
    assert rewrite_codex_segment(base + ' fork old', 'new') == base + ' resume new'
    assert strip_resume_from_codex_segment(base + ' fork old') == base
    assert rewrite_codex_segment(base, 'new') == base + ' resume new'


def test_continuation_probe_does_not_scan_other_subcommands_or_prompt():
    from provider_backends.codex.start_cmd_runtime.rewriting import _continuation_subcommand_index
    for tokens in [['codex', 'exec', 'fork'], ['codex', '--', 'fork'],
                   ['codex', 'prompt', 'resume']]:
        assert _continuation_subcommand_index(tokens, 0) is None


def test_rewrite_codex_segment_swaps_resume_id_without_keeping_old_continuation() -> None:
    rewritten = rewrite_codex_segment(
        'codex -c disable_paste_burst=true resume old-session',
        'new-session',
    )

    assert rewritten == 'codex -c disable_paste_burst=true resume new-session'


def test_rewrite_codex_segment_appends_resume_when_no_continuation_present() -> None:
    rewritten = rewrite_codex_segment('codex -m gpt-5.4', 'sess-123')

    assert rewritten == 'codex -m gpt-5.4 resume sess-123'


def test_strip_resume_from_codex_segment_removes_fork_continuation() -> None:
    stripped = strip_resume_from_codex_segment(
        'codex fork 00000000-0000-0000-0000-000000000001'
    )

    assert stripped == 'codex'


def test_build_resume_start_cmd_managed_remote_path_untouched_by_fork_fix() -> None:
    command = (
        "export CCB_CODEX_MANAGED_REMOTE=1 CCB_CODEX_RESUME_ID='old-id'; "
        'codex --remote unix:///tmp/app-server.sock'
    )

    rewritten = build_resume_start_cmd(command, 'new-id')

    assert 'CCB_CODEX_RESUME_ID=new-id' in rewritten
    assert 'CCB_CODEX_MANAGED_REMOTE=1' in rewritten
    assert 'fork' not in rewritten
    assert 'resume new-id' not in rewritten


def test_generated_hook_trust_resume_binding_is_idempotent() -> None:
    base = ('export CODEX_HOME=/tmp/test-home; codex -c disable_paste_burst=true '
            '--ask-for-approval never --sandbox danger-full-access '
            '--dangerously-bypass-hook-trust')
    expected = base + ' resume session-1'
    command = base
    for _ in range(5):
        command = build_resume_start_cmd(command, 'session-1')
        assert command == expected
    assert strip_resume_start_cmd(command) == base


def test_generated_hook_trust_repairs_previously_duplicated_resume_suffix() -> None:
    base = 'codex --dangerously-bypass-hook-trust'
    polluted = base + ' resume old-session' * 5
    assert build_resume_start_cmd(polluted, 'new-session') == base + ' resume new-session'


@pytest.mark.parametrize(
    ('args', 'expected'),
    [
        # Long flags, separated and attached values.
        (['codex', '--sandbox', 'read-only', 'resume', 's'], True),
        (['codex', '--sandbox=read-only', 'resume', 's'], True),
        (['codex', '--ask-for-approval', 'never', 'resume', 's'], True),
        (['codex', '--ask-for-approval=never', 'resume', 's'], True),
        # Short aliases from the codex CLI surface.
        (['codex', '-s', 'read-only', 'resume', 's'], True),
        (['codex', '-a', 'never', 'resume', 's'], True),
        (['codex', '-s=read-only', 'resume', 's'], True),
        # Standalone permission switches.
        (['codex', '--dangerously-bypass-hook-trust', 'resume', 's'], True),
        (['codex', '--dangerously-bypass-approvals-and-sandbox', 'resume', 's'], True),
        (['codex', '--approve-for-me', 'resume', 's'], True),
        # Config overrides that change permission behavior.
        (['codex', '-c', 'sandbox_mode=read-only', 'resume', 's'], True),
        (['codex', '-c', 'approval_policy=never', 'resume', 's'], True),
        (['codex', '-c', 'sandbox_mode=read-only', '--profile', 'x', 'resume', 's'], True),
        # Non-permission configuration must not block.
        (['codex', '-c', 'model=gpt-5', 'resume', 's'], False),
        # Option values are not subcommands.
        (['codex', '--model', 'resume', 's'], False),
        (['codex', '-m', 'resume', 's'], False),
        # resume must be the terminal continuation.
        (['codex', 'resume', 's', '--sandbox', 'read-only'], False),
        # Malformed and plain launches.
        (['codex', 'resume'], False),
        (['codex', '--profile', 'x', 'resume', 's'], False),
        (['codex', '--search', 'resume', 's'], False),
    ],
)
def test_remote_resume_blocked_by_permission_overrides_spellings(args, expected) -> None:
    from provider_backends.codex.launcher_runtime.command_runtime.service import (
        _remote_resume_blocked_by_permission_overrides,
    )

    assert _remote_resume_blocked_by_permission_overrides(args) is expected


@pytest.mark.parametrize('startup_args', [
    ('resume', 'user-session'), ('resume', '--last'), ('fork', 'user-session'),
    ('--profile', 'custom', 'resume', 'user-session'),
    ('--approve-for-me', 'resume', 'user-session'),
    ('-m', 'resume', 'fork', 'user-session'),
])
def test_explicit_codex_continuation_never_adds_automatic_restore(tmp_path: Path, startup_args):
    from provider_backends.codex.launcher_runtime.command_runtime.service import _codex_args

    def unexpected_lookup(*arguments, **keywords):
        raise AssertionError('Explicit continuation must not inspect CCB history')

    state = {}
    args = _codex_args(
        SimpleNamespace(auto_permission=True, restore=True),
        SimpleNamespace(role=None, restore_default=RestoreMode.AUTO, startup_args=startup_args),
        tmp_path, profile=None, provider_start_parts=['codex'],
        load_resume_session_id_fn=unexpected_lookup,
        load_linked_continuation_session_id_fn=unexpected_lookup,
        launch_context=state,
    )
    assert tuple(args[-len(startup_args):]) == startup_args
    assert state == {}


@pytest.mark.parametrize('startup_args', [
    ('--model', 'resume'), ('-mresume',), ('--profile=fork',), ('-p', 'fork'),
])
def test_codex_option_values_do_not_disable_automatic_restore(tmp_path: Path, startup_args):
    from provider_backends.codex.launcher_runtime.command_runtime.service import _codex_args

    args = _codex_args(
        SimpleNamespace(auto_permission=False, restore=True),
        SimpleNamespace(role=None, restore_default=RestoreMode.AUTO, startup_args=startup_args),
        tmp_path, profile=None, provider_start_parts=['codex'],
        load_resume_session_id_fn=lambda *arguments, **keywords: 'ccb-session',
    )
    assert args[-2:] == ['resume', 'ccb-session']


@pytest.mark.parametrize('startup_args', [
    ('resume', 'user-session'), ('resume', '--last'), ('fork', 'user-session'),
    ('resume', 'user-session', '--no-alt-screen'),
])
def test_explicit_codex_continuation_uses_native_cli(monkeypatch, tmp_path: Path, startup_args):
    from provider_backends.codex.launcher_runtime.command_runtime import service

    def unexpected_managed_command(*arguments, **keywords):
        raise AssertionError('Explicit CLI session controls must not use the managed remote bridge')

    monkeypatch.setattr(service, '_env_map', lambda *arguments, **keywords: {})
    state = {'project_root': str(tmp_path)}
    command = service.build_start_cmd(
        SimpleNamespace(auto_permission=False, restore=True),
        SimpleNamespace(
            role=None, name='codex', restore_default=RestoreMode.AUTO,
            startup_args=startup_args, provider_command_template=None,
        ),
        tmp_path, 'launch-id',
        load_resolved_provider_profile_fn=lambda runtime: None,
        prepare_codex_home_overrides_fn=lambda *arguments, **keywords: {},
        provider_start_parts_fn=lambda provider: ['codex'],
        load_resume_session_id_fn=unexpected_managed_command,
        build_codex_shell_prefix_fn=lambda **keywords: [],
        supports_managed_app_server_fn=lambda parts: True,
        build_managed_app_server_command_fn=unexpected_managed_command,
        prepared_state=state,
    )
    assert command.endswith(' '.join(startup_args))
    assert state['codex_app_server_enabled'] is False
