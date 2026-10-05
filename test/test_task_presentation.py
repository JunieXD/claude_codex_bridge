from types import SimpleNamespace

import pytest

from ccbd.project_view.service import _present_agent_tasks
from task_presentation import task_presentation


@pytest.mark.parametrize('phase,expected', [
    ('queued', 'queued'), ('injecting', 'waiting_start'), ('executing', 'running'),
    ('provider_idle_pending_terminal', 'waiting_result'), ('reply_queued', 'result_queued'),
    ('reply_delivering', 'result_delivering'), ('terminal', 'result_returned'),
    ('unknown', 'attention'), ('orphaned', 'attention'),
])
def test_task_phases_are_distinguished_without_claiming_queued_work_is_running(phase, expected):
    assert task_presentation({'execution_phase': phase})['task_status'] == expected


@pytest.mark.parametrize('reason,status', [
    ('composer_missing', 'input_unrecognized'), ('composer_layout_unknown', 'input_unrecognized'),
    ('waiting_for_draft', 'waiting_input'), ('provider_busy', 'waiting_provider'),
])
def test_wait_reason_and_long_unknown_notice(reason, status):
    payload = task_presentation({'agent_name': 'codex', 'delivery_wait': {'reason': reason, 'blocked_seconds': 60}})
    assert payload['task_status'] == status
    assert payload['task_status_warning'] == (status == 'input_unrecognized')
    assert 'Do not resubmit' in payload['task_status_notice']
    assert 'ccb pend --queue --detail codex' in payload['task_status_notice']


def test_provider_fault_overrides_execution_label():
    assert task_presentation({'activity_state': 'failed', 'execution_phase': 'executing'})['task_status'] == 'attention'


def test_project_view_uses_cached_wait_evidence_only():
    calls = []
    def snapshot(agent, job):
        calls.append((agent, job))
        return {'reason': 'composer_missing', 'blocked_seconds': 60}
    dispatcher = SimpleNamespace(_execution_service=SimpleNamespace(draft_wait_snapshot=snapshot))
    agents = [{'name': 'codex', 'current_job_id': 'job1', 'activity_state': 'pending'}]
    comms = [{'id': 'job1', 'execution_phase': 'queued', 'execution_phase_reason': 'queued_lineage_confirmed'}]
    _present_agent_tasks(agents, comms, dispatcher)
    assert calls == [('codex', 'job1')]
    assert agents[0]['task_status_label'] == 'input-unknown'
    assert agents[0]['task_status_warning'] is True


def test_idle_agent_can_show_returned_result():
    agents = [{'name': 'codex', 'activity_state': 'idle'}]
    comms = [{'id': 'job1', 'target': 'codex', 'execution_phase': 'terminal', 'execution_phase_reason': 'job_completed'}]
    _present_agent_tasks(agents, comms, SimpleNamespace())
    assert agents[0]['task_status'] == 'result_returned'


def test_current_work_without_bounded_comms_entry_is_not_reported_as_queued():
    agents = [{
        'name': 'codex', 'current_job_id': 'job1', 'current_job_status': 'running',
        'activity_state': 'active', 'queue_depth': 2,
    }]
    _present_agent_tasks(agents, [], SimpleNamespace())
    assert agents[0]['task_status'] == 'running'


@pytest.mark.parametrize('reason', ['reply_delivery_failed', 'reply_delivery_incomplete', 'reply_delivery_cancelled'])
def test_unsuccessful_reply_is_never_labeled_returned(reason):
    assert task_presentation({'execution_phase': 'terminal', 'execution_phase_reason': reason})['task_status'] == 'attention'


def test_project_view_exposes_warning_and_clears_it_when_wait_resolves(tmp_path):
    from ccbd.services.dispatcher import JobDispatcher
    from ccbd.services.registry import AgentRegistry
    from project.ids import compute_project_id
    from storage.paths import PathLayout
    from test_ccbd_project_view import _config, _runtime, _project_view_service, _submit, NOW

    root = tmp_path / 'project'
    root.mkdir()
    layout = PathLayout(root)
    project_id = compute_project_id(root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for name in config.agents:
        registry.upsert(_runtime(name, project_id=project_id))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    job_id = _submit(dispatcher, project_id, sender='user', target='agent1')
    waits = {job_id: {'reason': 'composer_missing', 'blocked_seconds': 60}}
    dispatcher._execution_service = SimpleNamespace(draft_wait_snapshot=lambda agent, job: waits.get(job, {}))

    def view():
        return _project_view_service(
            project_root=root, project_id=project_id, layout=layout,
            config=config, registry=registry, dispatcher=dispatcher,
        ).build_response()['view']

    blocked = view()
    assert blocked['agents'][0]['task_status_label'] == 'input-unknown'
    assert blocked['agents'][0]['task_status_warning'] is True
    assert 'do not resend' in blocked['namespace']['sidebar']['view']['tips'][0]
    waits.clear()
    resolved = view()
    assert resolved['agents'][0]['task_status'] == 'queued'
    assert resolved['agents'][0]['task_status_warning'] is False
    assert not any('do not resend' in tip for tip in resolved['namespace']['sidebar']['view']['tips'])
