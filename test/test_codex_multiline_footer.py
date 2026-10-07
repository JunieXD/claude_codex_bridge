"""Codex 0.159.2 adjacent status/hint rows and historical status evidence."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from provider_execution.draft_guard import DraftGuard
from provider_execution.draft_observation import inspect_screen


CAPTURES = json.loads((Path(__file__).parent / 'fixtures/composer/codex-fullscreen-01592.json').read_text())


@pytest.mark.parametrize('capture', CAPTURES, ids=lambda c: c['case'])
def test_actual_fullscreen_tmux_captures(capture):
    assert inspect_screen('codex', capture, binding='native').state == capture['expected']


def screen(body=(), *, footer=None, content=None):
    content = content if content is not None else '\x1b[2mAsk Codex to do anything\x1b[0m'
    footer = footer if footer is not None else [
        '  GPT-6.1-Sol xhigh · workspace · title',
        '  ? for shortcuts                         ⚠ 2 warnings · f2 to view',
    ]
    rows = [*body, '', '› ' + content, '', *footer]
    return {'text': '\n'.join(rows) + '\n', 'cursor_x': 2, 'cursor_y': len(body) + 1}


@pytest.mark.parametrize('model', ['GPT-6.1-Sol', '未来模型', 'local/custom'])
def test_adjacent_native_status_and_hint_release_empty(model):
    captured = screen(['• Worked for 5s'])
    captured['text'] = captured['text'].replace('GPT-6.1-Sol', model)
    assert inspect_screen('codex', captured, binding='pane').state == 'empty'


@pytest.mark.parametrize('body', [
    ['• 解释提示 (esc to interrupt)', '• Worked for 5s'],
    ['• 解释提示 (esc to interrupt)'],
    ['• Working (5s • esc to interrupt)', '• Worked for 5s'],
    ['• Working (5s •', '  esc to interrupt)', '• Worked for 5s'],
    ['• Working (5s • esc to interrupt)', '• Worked for', '  5s'],
    ['• Working (5s • esc to interrupt)', '  Worked for 5s • 22:25'],
    ['• Working (5s • esc to interrupt)', '  Worked for', '  5s • 22:25'],
])
def test_completed_or_ordinary_history_does_not_veto_empty(body):
    captured = screen(body, footer=['  ? for shortcuts'])
    assert inspect_screen('codex', captured, binding='pane').state == 'empty'


@pytest.mark.parametrize('body', [
    ['• Working (5s • esc to interrupt)'],
    ['• Running (5s • esc to interrupt)'],
    ['• Working (5s •', '  esc to interrupt)'],
    ['• Worked for 5s', '• Working (1s • esc to interrupt)'],
    ['• Reconnecting... 1/5'],
    ['• Working · 1 background terminal running · /ps to view'],
])
def test_native_active_status_still_blocks(body):
    assert inspect_screen('codex', screen(body), binding='pane').reason == 'provider_busy'


@pytest.mark.parametrize('footer', [
    ['  ? for shortcuts', '  remaining draft'],
    ['  GPT-6.1-Sol · workspace', '  unrecognized continuation'],
])
def test_arbitrary_indented_text_is_not_a_multiline_footer(footer):
    assert inspect_screen('codex', screen(footer=footer), binding='pane').state == 'unknown'


def test_multiline_footer_retains_editor_mode_block():
    captured = screen()
    captured['text'] += '  NORMAL\n'
    assert inspect_screen('codex', captured, binding='pane').state == 'unknown'


def test_native_footer_does_not_hide_human_draft():
    captured = screen(content='human draft', footer=[
        '  Context 42% left', '  ? for shortcuts     ⚠ 2 warnings · f2 to view'])
    assert inspect_screen('codex', captured, binding='pane').state == 'nonempty'


def test_opaque_multiline_footer_cannot_authorize_draft_clearing():
    assert inspect_screen('codex', screen(content='human draft'), binding='pane').state == 'unknown'


def test_guard_releases_completed_two_row_composer_without_clear():
    captured = screen(['• Worked for 5s'])
    clears = []
    target = SimpleNamespace(
        observe=lambda: inspect_screen('codex', captured, binding='pane'),
        clear=lambda observation: clears.append(observation),
    )
    assert DraftGuard().allows(target)
    assert not clears


def test_completed_multiline_screen_releases_same_fifo_head_once(tmp_path, monkeypatch):
    from test_input_draft_fifo import _guarded_dispatcher
    from test_unified_message_fifo import _ask, _turn_end_decision

    ctx, dispatcher, execution, target, _clock = _guarded_dispatcher(tmp_path, monkeypatch)
    captured = [screen(['• Working (5s • esc to interrupt)'])]
    target.observe = lambda: inspect_screen('codex', captured[0], binding='pane-1')
    first = dispatcher.submit(_ask('codex', 'claude', 'first', project_id=ctx.project_id, task_id='1')).jobs[0]
    second = dispatcher.submit(_ask('codex', 'claude', 'second', project_id=ctx.project_id, task_id='2')).jobs[0]
    dispatcher.tick()
    assert not execution.started
    assert dispatcher.queue('codex')['agent']['delivery_wait']['reason'] == 'provider_busy'
    captured[0] = screen(['• Worked for 5s'])
    for _ in range(3):
        dispatcher.tick()
    assert execution.started == [first.job_id]
    execution.arm_turn_end(first.job_id, _turn_end_decision())
    dispatcher.poll_completions()
    dispatcher.tick()
    assert execution.started[:2] == [first.job_id, second.job_id]
    assert execution.started.count(first.job_id) == execution.started.count(second.job_id) == 1
    assert target.clear_count == 0
