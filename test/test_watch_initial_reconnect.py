from io import StringIO
from types import SimpleNamespace

import pytest

from cli.services import watch_runtime
from cli.services.ask_runtime import watch as ask_watch
from cli.services.daemon_runtime.models import CcbdServiceError


def invoke_watch(kind, connect, clock, sleep):
    if kind == 'target':
        return list(watch_runtime.watch_target(
            SimpleNamespace(), SimpleNamespace(target='job-demo'),
            connect_mounted_daemon_fn=connect, reconnect_error_classes=(CcbdServiceError,),
            time_fn=clock, sleep_fn=sleep, timeout_seconds_fn=lambda:None,
            poll_interval_seconds_fn=lambda:1,
        ))[-1]
    return ask_watch.watch_ask_job(
        SimpleNamespace(), 'job-demo', StringIO(), timeout=0, emit_output=False,
        connect_mounted_daemon_fn=connect, reconnect_error_classes=(CcbdServiceError,),
        monotonic_fn=clock, sleep_fn=sleep, poll_interval_seconds_fn=lambda:1,
        timeout_seconds_fn=lambda:0, render_watch_batch_fn=lambda batch:(),
        write_lines_fn=lambda *arguments:None,
    )


@pytest.fixture(autouse=True)
def no_persisted_fallback(monkeypatch):
    for module in (watch_runtime, ask_watch):
        monkeypatch.setattr(module, 'load_persisted_terminal_watch_payload', lambda *arguments, **keywords:None)


@pytest.mark.parametrize('kind', ['target', 'ask'])
def test_initial_watch_waits_for_transient_startup_without_restarting(kind):
    attempts = []

    class ReadyClient:
        def watch(self, target, *, cursor):
            return {'job_id':target, 'agent_name':'codex', 'cursor':cursor,
                    'terminal':True, 'status':'completed', 'reply':'done'}

    def connect(context, *, allow_restart_stale):
        attempts.append(allow_restart_stale)
        if len(attempts) < 3:
            raise CcbdServiceError('project ccbd is starting; wait for keeper to finish startup')
        return SimpleNamespace(client=ReadyClient())

    batch = invoke_watch(kind, connect, lambda:0, lambda seconds:None)
    assert batch.status == 'completed'
    assert attempts == [False, False, False]


@pytest.mark.parametrize('kind', ['target', 'ask'])
def test_initial_watch_does_not_wait_forever_without_user_timeout(kind):
    current_time = [0.0]

    def connect(context, *, allow_restart_stale):
        raise CcbdServiceError('project ccbd is starting; wait for keeper to finish startup')

    def sleep(seconds):
        current_time[0] += seconds

    with pytest.raises(RuntimeError, match='connection timed out'):
        invoke_watch(kind, connect, lambda:current_time[0], sleep)
    assert 10 <= current_time[0] <= 12


@pytest.mark.parametrize('kind', ['target', 'ask'])
def test_initial_watch_stops_retrying_when_startup_becomes_explicit_shutdown(kind):
    attempts = []

    def connect(context, *, allow_restart_stale):
        attempts.append(1)
        if len(attempts) == 1:
            raise CcbdServiceError('project ccbd is starting')
        raise CcbdServiceError('project ccbd is unmounted; run `ccb` first')

    with pytest.raises(CcbdServiceError, match='unmounted'):
        invoke_watch(kind, connect, lambda:0, lambda seconds:None)
    assert len(attempts) == 2
