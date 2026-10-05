from __future__ import annotations

import json
import math
from pathlib import Path
import re
from threading import RLock
import time
from uuid import uuid4

from ccbd.api_models import JobStatus, TargetKind
from ccbd.runtime import write_log
from provider_backends.claude.cache_usage import usage_counts
from provider_hooks.activity import load_activity
from storage.atomic import atomic_write_json

INTERVAL_MS = 45 * 60_000
EXPIRY_MS = 58 * 60_000
MAX_ATTEMPTS = 8
MAX_OUTPUT_TOKENS = 2_048
MIN_CONTEXT_TOKENS = 20_000
MAX_CONTEXT_TOKENS = 250_000
_PENDING = {JobStatus.ACCEPTED, JobStatus.QUEUED, JobStatus.RUNNING}
_REASON = re.compile(r'^[a-z][a-z0-9_]{0,63}$')


def _timestamp(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('invalid cache timestamp')
    return float(value)


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def pending_codex_jobs(dispatcher, actor: str, *, since_ms: float) -> list:
    from datetime import datetime

    job_ids = []
    for kind, name in dispatcher._state.slots():
        if kind is not TargetKind.AGENT:
            continue
        job_ids.extend(dispatcher._state.queued_items_for(kind, name))
        active = dispatcher._state.active_job_for(kind, name)
        if active:
            job_ids.append(active)
    result = []
    for job_id in dict.fromkeys(job_ids):
        job = dispatcher.get(job_id)
        if job is None or job.provider != 'codex' or job.status not in _PENDING or job.cancel_requested_at:
            continue
        request = job.request
        if request.from_actor != actor or request.silence_on_success or request.message_type != 'ask':
            continue
        try:
            created_at = datetime.fromisoformat(job.created_at.replace('Z', '+00:00')).timestamp() * 1000
        except (ValueError, AttributeError):
            continue
        if created_at >= since_ms:
            result.append(job)
    return result


def build_claude_cache_handler(dispatcher, registry, *, now_fn=lambda: time.time() * 1000):
    lock = RLock()

    def identity(payload):
        actor = str(payload.get('actor') or '')
        runtime = registry.get(actor)
        if (runtime is None or runtime.provider != 'claude' or runtime.health in {'stopped', 'failed'}
                or runtime.state.value in {'stopped', 'stopping', 'failed'}):
            raise ValueError('cache caller is not a live Claude agent')
        runtime_dir = dispatcher._layout.agent_provider_runtime_dir(actor, 'claude')
        if Path(str(payload.get('runtime_dir') or '')).resolve() != runtime_dir.resolve():
            raise ValueError('cache runtime mismatch')
        session = _load(Path(runtime.session_file)) if runtime.session_file else {}
        launch_id = str(payload.get('launch_id') or '')
        if not launch_id or session.get('ccb_session_id') != launch_id:
            raise ValueError('cache launch mismatch')
        session_id = str(payload.get('session_id') or '')
        if not session_id or len(session_id) > 100:
            raise ValueError('invalid cache session')
        activity = load_activity(runtime_dir) or {}
        if activity.get('ccb_session_id') != launch_id or activity.get('provider_session_id') != session_id:
            raise ValueError('cache session mismatch')
        return actor, runtime_dir, launch_id, session_id, activity

    def record(actor, runtime_dir, state, reason, *, jobs=(), usage=None, attempt_id=None, helper_ttl=None):
        if not _REASON.fullmatch(reason):
            raise ValueError('invalid cache reason')
        payload = {
            'reason': reason,
            'session_id': state.get('session_id'),
            'attempt_count': state.get('attempt_count', 0),
            'attempt_id': attempt_id,
            'pending_jobs': [job.job_id for job in jobs],
        }
        observation = state.get('observation') or {}
        payload.update({key: observation.get(key) for key in ('model', 'request_started_at', 'window_started_at')})
        if usage is not None:
            payload['usage'] = usage
        if helper_ttl in {'5m', '1h'}:
            payload['configured_helper_ttl'] = helper_ttl
        if state.get('last_log') == payload:
            return
        state['last_log'] = payload
        timestamp = dispatcher._clock()
        write_log(dispatcher._layout.agent_logs_dir(actor) / 'cache-keepalive.log',
                  json.dumps({'timestamp': timestamp, 'component': 'claude_cache_keepalive', **payload}, separators=(',', ':')))
        if attempt_id:
            for job in jobs:
                dispatcher._append_event(job, 'claude_cache_keepalive', payload, timestamp=timestamp)

    def handle(payload: dict) -> dict:
        with lock, dispatcher._chain_transition_lock:
            actor, runtime_dir, launch_id, session_id, activity = identity(payload)
            path = runtime_dir / 'cache-keepalive.json'
            state = _load(path)
            corrupt = path.exists() and not state
            if state.get('session_id') != session_id:
                state = {'session_id': session_id, 'attempt_count': 0, 'output_tokens': 0}
            if state.get('launch_id') != launch_id:
                state.update({'launch_id': launch_id, 'observation': None, 'in_flight': None, 'blocked': False})
            if corrupt:
                state['blocked'] = True
            action = payload.get('action')
            now = now_fn()
            if action == 'observe':
                evidence = payload.get('evidence') or {}
                counts = usage_counts(evidence.get('usage'))
                previous = state.get('observation') or {}
                verified = False
                if evidence.get('reason') == 'verified_usage' and counts:
                    usage = evidence['usage']
                    if counts['cache_creation_input_tokens']:
                        verified = (usage.get('ephemeral_1h_input_tokens') == counts['cache_creation_input_tokens']
                                    and usage.get('ephemeral_5m_input_tokens') == 0)
                    elif counts['cache_read_input_tokens']:
                        verified = previous.get('verified_1h', False) and previous.get('model') == payload.get('model')
                state['observation'] = {
                    'verified_1h': bool(verified),
                    'model': payload.get('model'),
                    'generation': payload.get('generation'),
                    'request_started_at': _timestamp(payload.get('request_started_at')),
                    'window_started_at': _timestamp(payload.get('window_started_at')),
                    'context_tokens': sum(counts[name] for name in counts if name != 'output_tokens') if counts else 0,
                    'cache_read_tokens': counts['cache_read_input_tokens'] if counts else 0,
                }
                state.setdefault('window_started_at', state['observation']['window_started_at'])
                state['observation']['window_started_at'] = _timestamp(state['window_started_at'])
                reason = 'verified_1h' if verified else 'unverified_1h'
                record(actor, runtime_dir, state, reason, usage=counts)
                result = {**evidence, 'window_started_at': state['observation']['window_started_at']}
            elif action in {'status', 'acquire'}:
                observation = state.get('observation') or {}
                since = observation.get('window_started_at') or state.get('window_started_at')
                jobs = pending_codex_jobs(dispatcher, actor, since_ms=since) if since else []
                result = {
                    'allowed': False,
                    'reason': 'no_pending_codex',
                    'pending_jobs': [job.job_id for job in jobs],
                    'attempt_count': state.get('attempt_count', 0),
                    'output_tokens': state.get('output_tokens', 0),
                    'blocked': state.get('blocked', False),
                    'in_flight': bool(state.get('in_flight')),
                    'verified_1h': observation.get('verified_1h', False),
                    'window_started_at': state.get('window_started_at'),
                }
                if action == 'acquire':
                    age = now - observation.get('request_started_at', 0)
                    reason = 'ready'
                    if not jobs:
                        reason = 'no_pending_codex'
                    elif activity.get('state') not in {'idle', 'pending'}:
                        reason = 'main_busy'
                    elif not observation.get('verified_1h'):
                        reason = 'unverified_1h'
                    elif payload.get('helper_ttl') not in {'5m', '1h'}:
                        reason = 'unrecognized_helper_ttl'
                    elif any(payload.get(key) != observation.get(key) for key in ('generation', 'model', 'request_started_at', 'window_started_at')):
                        reason = 'stale_snapshot'
                    elif not MIN_CONTEXT_TOKENS <= observation.get('context_tokens', 0) <= MAX_CONTEXT_TOKENS:
                        reason = 'context_out_of_bounds'
                    elif payload.get('expected_tokens') != observation.get('context_tokens'):
                        reason = 'usage_mismatch'
                    elif age < 0 or age >= EXPIRY_MS:
                        reason = 'cache_expired'
                    elif observation.get('cache_read_tokens', 0) < observation.get('context_tokens', 0) * 0.95:
                        reason = 'main_prefix_unstable'
                    elif age < INTERVAL_MS:
                        reason = 'not_due'
                    elif state.get('blocked'):
                        reason = 'stopped'
                    elif state.get('in_flight'):
                        reason = 'in_flight'
                    elif state.get('attempt_count', 0) >= MAX_ATTEMPTS or state.get('output_tokens', 0) >= MAX_OUTPUT_TOKENS:
                        reason = 'budget_exhausted'
                    if reason == 'ready':
                        attempt_id = uuid4().hex
                        state['attempt_count'] = state.get('attempt_count', 0) + 1
                        state['in_flight'] = {
                            'attempt_id': attempt_id,
                            'job_ids': [job.job_id for job in jobs],
                            'generation': observation.get('generation'),
                            'context_tokens': observation.get('context_tokens'),
                            'helper_ttl': payload['helper_ttl'],
                        }
                        result.update({'allowed': True, 'attempt_id': attempt_id, 'attempt_count': state['attempt_count']})
                        record(actor, runtime_dir, state, 'attempt_started', jobs=jobs, attempt_id=attempt_id,
                               helper_ttl=payload['helper_ttl'])
                    result['reason'] = reason
            elif action == 'finish':
                attempt = state.get('in_flight') or {}
                if not attempt or attempt.get('attempt_id') != payload.get('attempt_id'):
                    return {'allowed': False, 'reason': 'stale_attempt'}
                counts = usage_counts(payload.get('usage'))
                reason = str(payload.get('reason') or 'missing_fork_usage')
                if counts:
                    state['output_tokens'] = state.get('output_tokens', 0) + counts['output_tokens']
                observation = state.get('observation') or {}
                if reason == 'cache_hit' and counts:
                    expected_tokens = attempt.get('context_tokens', 0)
                    if counts['cache_read_input_tokens'] < expected_tokens * 0.95:
                        reason = 'cache_miss'
                    elif counts['cache_creation_input_tokens'] + counts['input_tokens'] > expected_tokens * 0.05:
                        reason = 'expensive_tail'
                    elif counts['output_tokens'] > MAX_OUTPUT_TOKENS:
                        reason = 'excessive_output'
                elif reason == 'cache_hit':
                    reason = 'missing_fork_usage'
                if reason == 'cache_hit':
                    if attempt['generation'] == observation.get('generation'):
                        observation['request_started_at'] = _timestamp(payload.get('started_at'))
                elif reason != 'activity_race':
                    state['blocked'] = True
                state['in_flight'] = None
                jobs = [job for job_id in attempt['job_ids'] if (job := dispatcher.get(job_id)) is not None]
                record(actor, runtime_dir, state, reason, jobs=jobs, usage=counts, attempt_id=attempt['attempt_id'],
                       helper_ttl=attempt.get('helper_ttl'))
                result = {'allowed': reason == 'cache_hit', 'reason': reason}
            elif action == 'report':
                reason = str(payload.get('reason') or '')
                record(actor, runtime_dir, state, reason, usage=usage_counts(payload.get('usage')),
                       helper_ttl=payload.get('helper_ttl'))
                result = {'allowed': True}
            else:
                raise ValueError('unknown cache action')
            atomic_write_json(path, state)
            return result

    return handle
