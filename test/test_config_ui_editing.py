import json
import re
import shutil
import subprocess

import pytest

from cli.services.config_ui import config_ui_asset_path


def run_editor(body):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is needed for Config UI behavioral tests')
    page = config_ui_asset_path().read_text()
    functions = []
    for name in ['overlays', 'overlayFor', 'setOverlayField', 'thinkingLevels', 'parseStartupArgs']:
        match = re.search(rf'    function {name}\([^\n]*\) \{{\n.*?\n    \}}', page, re.S)
        assert match is not None
        functions.append(match.group(0))
    handlers = []
    for name in ['model', 'startup_args']:
        match = re.search(rf'      field\("{name}"\)\.addEventListener\("change", .*?\n      \}}\);', page, re.S)
        assert match is not None
        handlers.append(match.group(0))
    script = '''
const visualState = {document: {agents: {codex: {model: "gpt-6.1-sol", thinking: "ultra"}}}};
const leaf = {name: "codex", provider: "codex"};
const callbacks = {};
let mutations = 0, error = null, promptResult = null;
function field(name) {return {addEventListener(type, fn) {callbacks[name] = fn;}};}
function mutateVisual(fn) {mutations++; fn();}
function providerCapability() {return {static_thinking: true, custom_model: true, thinking_options: ["high", "ultra"], models: [{id: "gpt-6.1-sol", reasoning_levels: ["high", "ultra"]}]};}
function t(key) {return key;}
function setActionStatus(message) {error = message;}
const window = {prompt() {return promptResult;}};
''' + '\n'.join(functions + handlers) + body
    result = subprocess.run([node, '-e', script], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('prompt_result', [None, '', '  '])
def test_cancel_or_empty_custom_model_preserves_settings(prompt_result):
    result = run_editor(f'''
promptResult = {json.dumps(prompt_result)};
const event = {{target: {{value: "__custom__"}}}};
callbacks.model(event);
console.log(JSON.stringify({{overlay: overlayFor("codex"), mutations, selection: event.target.value}}));
''')
    assert result == {'overlay': {'model': 'gpt-6.1-sol', 'thinking': 'ultra'}, 'mutations': 0, 'selection': 'gpt-6.1-sol'}


def test_custom_model_keeps_compatible_thinking():
    result = run_editor('''
promptResult = "custom-model";
callbacks.model({target: {value: "__custom__"}});
console.log(JSON.stringify(overlayFor("codex")));
''')
    assert result == {'model': 'custom-model', 'thinking': 'ultra'}


def test_startup_args_round_trip_preserves_spaces_quotes_and_empty_values():
    original = ['-c', 'developer_instructions="review carefully"', '/path with spaces/hook.py', '']
    result = run_editor(f'''
const original = {json.dumps(original)};
callbacks.startup_args({{target: {{value: JSON.stringify(original)}}}});
console.log(JSON.stringify(overlayFor("codex").startup_args));
''')
    assert result == original


@pytest.mark.parametrize('text', ['--flag value', '{"flag":1}', '[1]', '[null]', '["bad\\u0000arg"]'])
def test_invalid_startup_args_do_not_mutate_config(text):
    result = run_editor(f'''
overlayFor("codex", true).startup_args = ["--existing"];
callbacks.startup_args({{target: {{value: {json.dumps(text)}}}}});
console.log(JSON.stringify({{args: overlayFor("codex").startup_args, mutations, error}}));
''')
    assert result['args'] == ['--existing']
    assert result['mutations'] == 0
    assert result['error']


@pytest.mark.parametrize('text', ['', '[]'])
def test_empty_startup_args_return_to_inheritance(text):
    result = run_editor(f'''
overlayFor("codex", true).startup_args = ["--existing"];
callbacks.startup_args({{target: {{value: {json.dumps(text)}}}}});
console.log(JSON.stringify(overlayFor("codex")));
''')
    assert 'startup_args' not in result
