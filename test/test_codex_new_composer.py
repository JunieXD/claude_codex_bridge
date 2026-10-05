import pytest

from provider_execution.draft_observation import inspect_screen


@pytest.mark.parametrize('prompt', ['›', '»'])
@pytest.mark.parametrize('two_rows', [False, True])
@pytest.mark.parametrize('content,expected', [
    ('Ask Codex to do anything', 'empty'),
    ('Ask Codex to do anything   ', 'empty'),
    ('implement the plan', 'nonempty'),
    ('Ask Codex to do anything plus draft', 'nonempty'),
    ('draft\n  second line', 'nonempty'),
])
def test_codex_prompt_and_footer_versions(prompt, two_rows, content, expected):
    footer = '  ? for shortcuts'
    if two_rows:
        footer = '  custom/model ultra \x1b[2m·\x1b[22m /workspace\n' + footer
    screen = {'text': f'{prompt} {content}\n\n{footer}', 'cursor_x': 2, 'cursor_y': 0}
    assert inspect_screen('codex', screen, binding='test').state == expected


@pytest.mark.parametrize('prompt', ['›', '»'])
def test_new_codex_footer_does_not_hide_unstyled_draft(prompt):
    screen = {
        'text': f'{prompt} Ask Codex to do anything\n\n  user draft · not a status\n  ? for shortcuts',
        'cursor_x': 2,
        'cursor_y': 0,
    }
    assert inspect_screen('codex', screen, binding='test').state == 'unknown'


@pytest.mark.parametrize('prompt', ['›', '»'])
@pytest.mark.parametrize('prefix,suffix', [
    ('• Working (1s • esc to interrupt)\n', ''),
    ('', '  NORMAL'),
])
def test_new_codex_busy_or_editor_mode_blocks_delivery(prompt, prefix, suffix):
    screen = {
        'text': prefix + f'{prompt} Ask Codex to do anything\n\n'
        '  custom/model ultra \x1b[2m·\x1b[22m /workspace\n  ? for shortcuts' + suffix,
        'cursor_x': 2,
        'cursor_y': int(bool(prefix)),
    }
    assert inspect_screen('codex', screen, binding='test').state == 'unknown'

