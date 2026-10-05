from __future__ import annotations

from collections.abc import Mapping


def task_presentation(payload: Mapping) -> dict:
    wait = payload.get('delivery_wait')
    reason = str(payload.get('execution_phase_reason') or payload.get('activity_reason') or '')
    phase = str(payload.get('execution_phase') or '')
    status = 'idle'
    label = 'idle'
    notice = None
    warning = False
    if payload.get('activity_state') == 'failed':
        status, label = 'attention', 'check'
        reason = str(payload.get('activity_reason') or reason)
    elif payload.get('activity_state') == 'offline' or payload.get('runtime_state') == 'stopped':
        status, label = 'offline', 'offline'
    elif isinstance(wait, Mapping) and wait:
        reason = str(wait.get('reason') or 'unknown')
        if reason == 'provider_busy':
            status, label = 'waiting_provider', 'wait-provider'
        elif reason in {'waiting_for_draft', 'clear_unconfirmed', 'draft_changed_before_clear'}:
            status, label = 'waiting_input', 'input-held'
        else:
            status, label = 'input_unrecognized', 'input-unknown'
            elapsed = wait.get('blocked_seconds')
            warning = isinstance(elapsed, (int, float)) and elapsed >= 30
        name = payload.get('agent_name') or payload.get('name') or payload.get('target') or '<agent>'
        notice = f'Inspect {name} pane; run ccb pend --queue --detail {name}. Do not resubmit this queued request.'
    elif phase in {'orphaned', 'unknown'}:
        status, label = 'attention', 'check'
    elif phase == 'queued' or (not phase and (
        payload.get('current_job_status') in {'accepted', 'queued'}
        or payload.get('activity_reason') == 'job_queued'
        or (payload.get('queue_depth', 0) and payload.get('activity_state') != 'active'
            and payload.get('current_job_status') != 'running')
    )):
        status, label = 'queued', 'queued'
    elif phase == 'injecting':
        status, label = 'waiting_start', 'wait-start'
        notice = 'Request submitted; provider execution is not yet confirmed. Do not send it again.'
    elif phase == 'executing':
        status, label = 'running', 'running'
    elif phase == 'provider_idle_pending_terminal':
        status, label = 'waiting_result', 'wait-result'
    elif phase == 'reply_queued':
        status, label = 'result_queued', 'result-queued'
    elif phase == 'reply_delivering':
        status, label = 'result_delivering', 'result-sending'
    elif phase == 'terminal':
        if reason in {
            'job_failed', 'job_incomplete', 'job_cancelled',
            'reply_delivery_failed', 'reply_delivery_incomplete', 'reply_delivery_cancelled',
        }:
            status, label = 'attention', 'check'
        else:
            status, label = 'result_returned', 'returned'
    elif payload.get('activity_state') == 'active':
        status, label = 'running', 'running'
    elif payload.get('activity_state') == 'pending':
        status, label = 'waiting_start', 'wait-start'
    return {
        'task_status': status, 'task_status_label': label, 'task_status_reason': reason,
        'task_status_notice': notice, 'task_status_warning': warning,
    }
