from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, JobStatus, MessageEnvelope
from ccbd.services.dispatcher import DispatchError, JobDispatcher
from ccbd.services.registry import AgentRegistry
from completion.models import CompletionSourceKind
from provider_execution.base import ProviderSubmission
from provider_execution.common_runtime.terminal import interrupt_and_clear_runtime_target
from provider_execution.registry import ProviderExecutionRegistry
from provider_execution.service import ExecutionCancellationError, ExecutionService, _cancel_submission
from storage.paths import PathLayout
from test_v2_ccbd_dispatcher import _bootstrap_test_project, _fake_config, _runtime


class FixtureBackend:
    def __init__(self):
        self.available = False

    def send_key(self, pane, key):
        return self.available

    def send_text(self, pane, text):
        if not self.available:
            raise ConnectionError('terminal unavailable')


class FixtureAdapter:
    provider = 'fake'

    def __init__(self, backend):
        self.backend = backend

    def start(self, job, *, context, now):
        return ProviderSubmission(
            job_id=job.job_id, agent_name=job.agent_name, provider=self.provider,
            accepted_at=now, ready_at=now, source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reply='', runtime_state={'mode':'active', 'backend':self.backend, 'pane_id':'fixture', 'prompt_sent':True},
        )


def test_cancel_failure_preserves_running_job_and_execution_until_retry(tmp_path):
    project = _bootstrap_test_project(tmp_path / 'repo')
    layout = PathLayout(project.project_root)
    config = _fake_config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=project.project_id, layout=layout, pid=101))
    backend = FixtureBackend()
    execution = ExecutionService(ProviderExecutionRegistry([FixtureAdapter(backend)]), clock=lambda:'2026-10-05T00:00:00Z')
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution)
    receipt = dispatcher.submit(MessageEnvelope(
        project_id=project.project_id, to_agent='demo', from_actor='user', body='work',
        task_id=None, reply_to=None, message_type='ask', delivery_scope=DeliveryScope.SINGLE,
    ))
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    removed = []
    execution._runtime_state.state_store = SimpleNamespace(remove=lambda job: removed.append(job))
    with pytest.raises(DispatchError, match='still tracked'):
        dispatcher.cancel(job_id)
    assert dispatcher.get(job_id).status is JobStatus.RUNNING
    assert registry.get('demo').state.value == 'busy'
    assert job_id in execution._active
    assert not removed
    backend.available = True
    assert dispatcher.cancel(job_id).status is JobStatus.CANCELLED
    assert job_id not in execution._active
    assert removed == [job_id]


def test_successful_clear_without_interrupt_does_not_report_cancellation():
    backend = SimpleNamespace(send_key=lambda pane, key: key == 'C-u', send_text=lambda *arguments: False)
    assert interrupt_and_clear_runtime_target(backend, 'fixture') is False


@pytest.mark.parametrize('native_success', [False, True])
def test_native_cancel_can_succeed_without_a_terminal(native_success):
    submission = ProviderSubmission(
        job_id='fixture', agent_name='demo', provider='fake', accepted_at='', ready_at='',
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG, reply='', runtime_state={'mode':'active'},
    )
    adapter = SimpleNamespace(cancel=lambda submission: native_success)
    if native_success:
        _cancel_submission(adapter, submission)
    else:
        with pytest.raises(ExecutionCancellationError):
            _cancel_submission(adapter, submission)
