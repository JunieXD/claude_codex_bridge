from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event, RLock, Thread
from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope, TargetKind
from ccbd.handlers.claude_cache import build_claude_cache_handler, INTERVAL_MS, EXPIRY_MS
from ccbd.services.dispatcher_runtime.state import DispatcherState
from ccbd.socket_server import CcbdSocketServer
from provider_backends.claude.cache_keepalive import (
    configure_cache_keepalive,
    launch_warnings,
    read_launch_status,
    write_launch_status,
)
from provider_hooks.activity import write_activity
from storage.paths import PathLayout


START = 1791158400000
MAIN_USAGE = {'input_tokens': 10, 'output_tokens': 1, 'cache_creation_input_tokens': 500,
              'cache_read_input_tokens': 19500, 'ephemeral_1h_input_tokens': 500, 'ephemeral_5m_input_tokens': 0}
HIT_USAGE = {'input_tokens': 10, 'output_tokens': 1, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 20000}


def timestamp(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat()


@pytest.fixture
def rig(tmp_path):
    layout = PathLayout(tmp_path / 'project')
    runtime_dir = layout.agent_provider_runtime_dir('claude', 'claude')
    runtime_dir.mkdir(parents=True)
    session = runtime_dir / 'session.json'
    session.write_text(json.dumps({'ccb_session_id': 'launch'}))
    runtime = SimpleNamespace(provider='claude', health='healthy', state=SimpleNamespace(value='idle'), session_file=str(session))
    registry = SimpleNamespace(get=lambda actor: runtime if actor == 'claude' else None)
    state = DispatcherState(['claude', 'codex'])
    jobs = {}
    events = []
    dispatcher = SimpleNamespace(_layout=layout, _state=state, get=jobs.get, _chain_transition_lock=RLock(),
                                 _clock=lambda: timestamp(START), _append_event=lambda job, kind, payload, **kw: events.append((job.job_id, kind, payload)))
    clock = [START + INTERVAL_MS]
    handler = build_claude_cache_handler(dispatcher, registry, now_fn=lambda: clock[0])
    payload = {'actor': 'claude', 'launch_id': 'launch', 'runtime_dir': str(runtime_dir), 'session_id': 'native',
               'model': 'claude-opus-5-5', 'generation': 2, 'request_started_at': START,
               'window_started_at': START - 1000, 'helper_ttl': '1h', 'expected_tokens': 20010}
    write_activity(provider='claude', project_id=layout.project_id, agent_name='claude', runtime_dir=runtime_dir,
                   state='idle', source='test', ccb_session_id='launch', provider_session_id='native')

    def add_job(identifier='child', **changes):
        request = MessageEnvelope(layout.project_id, 'codex', changes.pop('sender', 'claude'), 'private task', None, None,
                                  changes.pop('message_type', 'ask'), DeliveryScope.SINGLE, changes.pop('silence', False))
        status = changes.pop('status', JobStatus.RUNNING)
        job = JobRecord(identifier, None, 'codex', changes.pop('provider', 'codex'), request, status,
                        {} if status not in {JobStatus.ACCEPTED, JobStatus.QUEUED, JobStatus.RUNNING} else None,
                        changes.pop('cancel_requested_at', None), timestamp(changes.pop('created', START)), timestamp(START))
        jobs[identifier] = job
        state.record(job)
        if status is JobStatus.RUNNING:
            state.mark_active_for(TargetKind.AGENT, 'codex', identifier)
        elif status in {JobStatus.QUEUED, JobStatus.ACCEPTED}:
            state.enqueue_for(TargetKind.AGENT, 'codex', identifier)
        return job

    def call(action, **extra):
        return handler({**payload, 'action': action, **extra})

    call('observe', evidence={'reason': 'verified_usage', 'usage': MAIN_USAGE})
    return SimpleNamespace(call=call, handler=handler, payload=payload, add_job=add_job, clock=clock,
                           runtime=runtime, runtime_dir=runtime_dir, layout=layout, events=events, session=session,
                           dispatcher=dispatcher, registry=registry)


def test_one_due_waiting_request_gets_a_single_lease(rig):
    rig.add_job()
    lease = rig.call('acquire')
    assert lease['allowed'] and lease['attempt_count'] == 1
    assert rig.call('acquire')['reason'] == 'in_flight'
    result = rig.call('finish', attempt_id=lease['attempt_id'], reason='cache_hit', usage=HIT_USAGE, started_at=rig.clock[0])
    assert result == {'allowed': True, 'reason': 'cache_hit'}
    assert rig.call('finish', attempt_id=lease['attempt_id'], reason='cache_hit', usage=HIT_USAGE)['reason'] == 'stale_attempt'
    assert rig.call('acquire', request_started_at=rig.clock[0])['reason'] == 'not_due'
    assert len(rig.events) == 2


@pytest.mark.parametrize('changes', [
    {'sender': 'user'}, {'provider': 'gemini'}, {'silence': True}, {'message_type': 'reply'},
    {'cancel_requested_at': 'now'}, {'created': START - 2000}, {'status': JobStatus.COMPLETED},
])
def test_unrelated_or_finished_jobs_do_not_warm(rig, changes):
    rig.add_job(**changes)
    assert rig.call('acquire')['reason'] == 'no_pending_codex'


def test_queued_codex_work_and_multiple_children_are_supported(rig):
    rig.add_job('first', status=JobStatus.QUEUED)
    rig.add_job('second', status=JobStatus.QUEUED)
    lease = rig.call('acquire')
    assert lease['allowed'] and lease['pending_jobs'] == ['first', 'second']


@pytest.mark.parametrize('changes,reason', [
    ({'helper_ttl': None}, 'unrecognized_helper_ttl'), ({'generation': 10}, 'stale_snapshot'),
    ({'model': 'different'}, 'stale_snapshot'), ({'expected_tokens': 1}, 'usage_mismatch'),
])
def test_snapshot_and_ttl_checks_are_server_enforced(rig, changes, reason):
    rig.add_job()
    assert rig.call('acquire', **changes)['reason'] == reason


def test_existing_five_minute_helpers_are_supported_and_logged_without_ttl_overrides(rig):
    rig.add_job()
    lease = rig.call('acquire', helper_ttl='5m')
    assert lease['allowed']
    rig.call('finish', attempt_id=lease['attempt_id'], reason='cache_hit', usage=HIT_USAGE, started_at=rig.clock[0])
    rows = [json.loads(line) for line in (rig.layout.agent_logs_dir('claude') / 'cache-keepalive.log').read_text().splitlines()]
    assert rows[-1]['configured_helper_ttl'] == '5m'
    assert rows[-2]['configured_helper_ttl'] == '5m'


@pytest.mark.parametrize('age,reason', [(1, 'not_due'), (-1, 'cache_expired'), (EXPIRY_MS, 'cache_expired')])
def test_never_warms_early_or_after_sleep_expiry(rig, age, reason):
    rig.add_job()
    rig.clock[0] = START + age
    assert rig.call('acquire')['reason'] == reason


@pytest.mark.parametrize('field,value', [('launch_id', 'old'), ('session_id', 'other'), ('actor', 'codex'), ('runtime_dir', '/tmp/elsewhere')])
def test_stale_or_foreign_callers_are_rejected(rig, field, value):
    with pytest.raises(ValueError):
        rig.call('acquire', **{field: value})


def test_miss_stops_future_attempts_and_logs_no_task_content(rig):
    rig.add_job()
    lease = rig.call('acquire')
    miss = {**HIT_USAGE, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 20000}
    assert rig.call('finish', attempt_id=lease['attempt_id'], reason='cache_hit', usage=miss, started_at=rig.clock[0])['reason'] == 'cache_miss'
    assert rig.call('acquire')['reason'] == 'stopped'
    log = (rig.layout.agent_logs_dir('claude') / 'cache-keepalive.log').read_text()
    assert 'cache_miss' in log and 'private task' not in log


def test_ledger_survives_daemon_handler_recreation(rig):
    rig.add_job()
    lease = rig.call('acquire')
    fresh = build_claude_cache_handler(rig.dispatcher, rig.registry, now_fn=lambda: rig.clock[0])
    assert json.loads((rig.runtime_dir / 'cache-keepalive.json').read_text())['in_flight']['attempt_id'] == lease['attempt_id']
    assert fresh({**rig.payload, 'action': 'acquire'})['reason'] == 'in_flight'


def test_mixed_or_five_minute_writes_disable_keepalive(rig):
    rig.add_job()
    mixed = {**MAIN_USAGE, 'ephemeral_1h_input_tokens': 400, 'ephemeral_5m_input_tokens': 100}
    rig.call('observe', evidence={'reason': 'verified_usage', 'usage': mixed})
    assert rig.call('acquire')['reason'] == 'unverified_1h'


def test_unverified_keepalive_is_not_loaded_by_default(monkeypatch):
    monkeypatch.delenv('CCB_CLAUDE_CACHE_KEEPALIVE', raising=False)
    parts = ['claude']
    environment = {}
    configure_cache_keepalive(parts, environment)
    assert parts == ['claude']
    assert environment == {}


@pytest.mark.parametrize('ttl', [None, '5m', '1h'])
def test_experimental_bridge_launcher_does_not_touch_cache_ttls(monkeypatch, ttl):
    monkeypatch.delenv('CCB_CLAUDE_CACHE_KEEPALIVE', raising=False)
    parts = ['claude']
    environment = {'CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL': ttl} if ttl is not None else {}
    inherited = dict(environment)
    explicit = {'CCB_CLAUDE_CACHE_KEEPALIVE': '1'}
    if ttl is not None:
        explicit['CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL'] = ttl
    configure_cache_keepalive(parts, environment, extra_env=explicit)
    assert parts[1] == '--plugin-dir'
    assert Path(parts[2], '.claude-plugin/plugin.json').exists()
    assert Path(json.loads(environment['CCB_CLAUDE_CACHE_BRIDGE'])[1]).exists()
    assert environment == {**inherited, 'CCB_CLAUDE_CACHE_BRIDGE': environment['CCB_CLAUDE_CACHE_BRIDGE'], 'CLAUDE_CODE_ENABLE_FUNCTION_HOOKS': '1'}
    disabled = ['claude']
    configure_cache_keepalive(disabled, {}, extra_env={'CCB_CLAUDE_CACHE_KEEPALIVE': '0'})
    assert disabled == ['claude']


def test_explicit_five_minute_agent_setting_is_not_promoted(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL', '1h')
    environment = {}
    configure_cache_keepalive(['claude'], environment, extra_env={
        'CCB_CLAUDE_CACHE_KEEPALIVE': '1', 'CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL': '5m',
    })
    assert 'CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL' not in environment


def test_agent_can_disable_an_inherited_experimental_opt_in(monkeypatch):
    monkeypatch.setenv('CCB_CLAUDE_CACHE_KEEPALIVE', '1')
    parts = ['claude']
    environment = {}
    configure_cache_keepalive(parts, environment, extra_env={'CCB_CLAUDE_CACHE_KEEPALIVE': '0'})
    assert parts == ['claude']
    assert environment == {}


@pytest.mark.parametrize('flag', ['--bare', '--safe-mode'])
def test_bare_and_safe_modes_are_not_given_a_mod(flag):
    parts = ['claude']
    environment = {}
    configure_cache_keepalive(parts, environment, extra_env={'CCB_CLAUDE_CACHE_KEEPALIVE': '1'}, startup_args=(flag,))
    assert parts == ['claude']
    assert environment == {}


def test_an_explicit_function_hook_disable_is_respected(monkeypatch):
    monkeypatch.setenv('CLAUDE_CODE_ENABLE_FUNCTION_HOOKS', '0')
    parts = ['claude']
    environment = {}
    configure_cache_keepalive(parts, environment, extra_env={'CCB_CLAUDE_CACHE_KEEPALIVE': '1'})
    assert parts == ['claude']
    assert environment == {}


def test_unstable_main_prefix_is_not_warmed_despite_a_real_one_hour_write(rig):
    rig.add_job()
    unstable = {**MAIN_USAGE, 'cache_read_input_tokens': 0, 'cache_creation_input_tokens': 20000, 'ephemeral_1h_input_tokens': 20000}
    rig.call('observe', evidence={'reason': 'verified_usage', 'usage': unstable})
    assert rig.call('acquire')['reason'] == 'main_prefix_unstable'
    assert rig.call('status')['attempt_count'] == 0


def test_an_ordinary_followup_does_not_orphan_still_running_delegated_work(rig):
    rig.add_job()
    later = START + 5 * 60_000
    rig.payload['request_started_at'] = later
    rig.payload['generation'] += 1
    rig.call('observe', evidence={'reason': 'verified_usage', 'usage': MAIN_USAGE})
    rig.clock[0] = later + INTERVAL_MS
    assert rig.call('acquire')['allowed']
    assert rig.call('status')['window_started_at'] == START - 1000


def test_resumed_native_session_can_recover_its_existing_delegation_window(rig):
    rig.add_job()
    rig.session.write_text(json.dumps({'ccb_session_id': 'resumed'}))
    rig.payload['launch_id'] = 'resumed'
    write_activity(provider='claude', project_id=rig.layout.project_id, agent_name='claude', runtime_dir=rig.runtime_dir,
                   state='idle', source='test', ccb_session_id='resumed', provider_session_id='native')
    assert rig.call('status')['window_started_at'] == START - 1000
    assert rig.call('acquire')['reason'] == 'unverified_1h'


def test_observation_recovers_the_window_if_startup_status_was_unavailable(rig):
    rig.add_job()
    later_window = START + 5 * 60_000
    evidence = rig.call('observe', window_started_at=later_window, evidence={'reason': 'verified_usage', 'usage': MAIN_USAGE})
    assert evidence['window_started_at'] == START - 1000
    assert rig.call('acquire')['allowed']


def test_budget_limits_are_persisted_and_cannot_be_reset_by_main_turns(rig):
    rig.add_job()
    for attempt in range(8):
        started_at = START + (attempt + 1) * INTERVAL_MS
        rig.clock[0] = started_at
        rig.payload['request_started_at'] = START + attempt * INTERVAL_MS
        lease = rig.call('acquire')
        assert lease['allowed']
        rig.call('finish', attempt_id=lease['attempt_id'], reason='cache_hit', usage=HIT_USAGE, started_at=started_at)
    rig.clock[0] += INTERVAL_MS
    rig.payload['request_started_at'] += INTERVAL_MS
    assert rig.call('acquire')['reason'] == 'budget_exhausted'
    rig.call('observe', evidence={'reason': 'verified_usage', 'usage': MAIN_USAGE})
    assert rig.call('acquire')['reason'] == 'budget_exhausted'


def test_completed_child_during_a_fork_is_logged_without_another_attempt(rig):
    job = rig.add_job()
    lease = rig.call('acquire')
    job.status = JobStatus.COMPLETED
    rig.call('finish', attempt_id=lease['attempt_id'], reason='cache_hit', usage=HIT_USAGE, started_at=rig.clock[0])
    assert rig.call('acquire')['reason'] == 'no_pending_codex'
    assert rig.events[-1][0] == job.job_id


def test_unknown_response_cost_and_corrupt_ledger_fail_closed(rig):
    rig.add_job()
    lease = rig.call('acquire')
    rig.call('finish', attempt_id=lease['attempt_id'], reason='keepalive_error')
    assert rig.call('acquire')['reason'] == 'stopped'
    (rig.runtime_dir / 'cache-keepalive.json').write_text('broken JSON')
    rig.call('observe', evidence={'reason': 'verified_usage', 'usage': MAIN_USAGE})
    assert rig.call('acquire')['reason'] == 'stopped'


def test_real_bridge_socket_usage_lease_and_log_integration(rig, tmp_path):
    rig.add_job()
    projects = tmp_path / 'transcripts'
    projects.mkdir()
    transcript = projects / 'native.jsonl'
    counts = {key: MAIN_USAGE[key] for key in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')}
    transcript.write_text(json.dumps({
        'type': 'assistant', 'sessionId': 'native', 'timestamp': timestamp(START + 1),
        'message': {'model': 'claude-opus-5-5', 'content': [{'type': 'text', 'text': 'Private fake conversation content'}],
                    'usage': {**counts, 'cache_creation': {'ephemeral_1h_input_tokens': 500, 'ephemeral_5m_input_tokens': 0}}},
    }) + '\n')
    server = CcbdSocketServer(rig.layout.ccbd_socket_path)
    server.register_handler('claude_cache', rig.handler)
    server.listen()
    serving = Event()
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01, on_serving=serving.set), daemon=True)
    thread.start()
    assert serving.wait(3)
    bridge = Path(__file__).resolve().parents[1] / 'bin' / 'ccb-claude-cache.py'
    environment = {**os.environ, 'CCB_CALLER_PROJECT_ROOT': str(rig.layout.project_root),
                   'CCB_CALLER_ACTOR': 'claude', 'CCB_SESSION_ID': 'launch',
                   'CCB_CALLER_RUNTIME_DIR': str(rig.runtime_dir), 'CLAUDE_PROJECTS_ROOT': str(projects)}

    def call(action, **extra):
        request = {key: value for key, value in rig.payload.items() if key not in {'actor', 'launch_id', 'runtime_dir'}}
        result = subprocess.run([sys.executable, str(bridge)], input=json.dumps({**request, 'action': action, **extra}),
                                capture_output=True, text=True, timeout=10, env=environment)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Private fake' not in result.stdout
        return json.loads(result.stdout)

    try:
        observation = call('observe', transcript_path=str(transcript), main_usage=counts)
        assert observation['reason'] == 'verified_usage'
        lease = call('acquire')
        assert lease['allowed'] and lease['attempt_count'] == 1
        assert call('acquire')['reason'] == 'in_flight'
        assert call('finish', attempt_id=lease['attempt_id'], reason='cache_hit', usage=HIT_USAGE,
                    started_at=rig.clock[0])['reason'] == 'cache_hit'
        assert call('status')['attempt_count'] == 1
        log = rig.layout.agent_logs_dir('claude').joinpath('cache-keepalive.log').read_text()
        assert 'attempt_started' in log and 'cache_hit' in log and 'Private fake' not in log
        assert rig.events[-1][1] == 'claude_cache_keepalive'
    finally:
        server.shutdown()
        thread.join(3)
    assert not thread.is_alive()


def test_configure_reports_the_launch_decision(monkeypatch):
    monkeypatch.delenv('CCB_CLAUDE_CACHE_KEEPALIVE', raising=False)
    assert configure_cache_keepalive(['claude'], {}) == 'disabled'
    assert configure_cache_keepalive(['claude'], {}, extra_env={'CCB_CLAUDE_CACHE_KEEPALIVE': '1'}) == 'enabled'
    assert configure_cache_keepalive(
        ['claude'], {}, extra_env={'CCB_CLAUDE_CACHE_KEEPALIVE': '1'}, startup_args=('--bare',),
    ) == 'bare_or_safe_mode'


def test_launch_warning_when_shell_flag_never_reached_ccbd(tmp_path):
    runtime = tmp_path / 'claude'
    write_launch_status(runtime, 'disabled')
    assert read_launch_status(runtime) == 'disabled'
    [warning] = launch_warnings({'claude': runtime}, shell_env={'CCB_CLAUDE_CACHE_KEEPALIVE': '1'})
    assert '[agents.claude.env]' in warning
    assert launch_warnings({'claude': runtime}, shell_env={}) == []


def test_launch_warning_for_requested_but_unloaded_mod(tmp_path):
    loaded, blocked, unknown = tmp_path / 'a', tmp_path / 'b', tmp_path / 'c'
    write_launch_status(loaded, 'enabled')
    write_launch_status(blocked, 'hooks_disabled')
    warnings = launch_warnings({'a': loaded, 'b': blocked, 'c': unknown}, shell_env={})
    assert warnings == ["Cache keepalive was requested for Claude agent 'b' but not loaded: hooks_disabled."]
