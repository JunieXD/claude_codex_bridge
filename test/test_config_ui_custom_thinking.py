import json
import re
import shutil
import subprocess

import pytest

from cli.services.config_ui import config_ui_asset_path, config_ui_provider_capabilities


def test_codex_catalog_accepts_current_and_future_visible_models(tmp_path):
    cache = tmp_path / 'models.json'
    cache.write_text(json.dumps({'models': [
        {'slug': 'gpt-6.1-sol', 'visibility': 'list',
         'supported_reasoning_levels': [{'effort': 'high'}, {'effort': 'ultra'}]},
        {'slug': 'future-model', 'visibility': 'list',
         'supported_reasoning_levels': [{'effort': 'medium'}]},
        {'slug': 'hidden-model', 'visibility': 'hide'},
    ]}))
    payload = config_ui_provider_capabilities(
        environ={'HOME': str(tmp_path), 'PATH': ''}, codex_models_path=cache,
        cli_models={'opencode': [], 'mimo': []}, roles=[],
    )
    codex = next(provider for provider in payload['providers'] if provider['id'] == 'codex')
    assert [model['id'] for model in codex['models']] == ['gpt-6.1-sol', 'future-model']
    assert codex['models'][0]['reasoning_levels'] == ['high', 'ultra']
    assert 'ultra' in codex['thinking_options']


@pytest.mark.parametrize('model_id,expected,enabled', [
    ('custom-model', ['low', 'high', 'ultra'], True),
    ('inherit', [], False),
    ('known-model', ['high'], True),
    ('no-reasoning', [], False),
])
def test_thinking_widget_handles_custom_models(model_id, expected, enabled):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is needed for Config UI behavioral tests')
    page = config_ui_asset_path().read_text()
    functions = []
    for name in ['thinkingLevels', 'syncThinking']:
        match = re.search(rf'    function {name}\([^\n]+\) \{{\n.*?\n    \}}', page, re.S)
        assert match is not None
        functions.append(match.group(0))
    script = '\n'.join(functions) + '''
const widget = {
  dataset: {current: "ultra"}, options: [],
  replaceChildren(...items) {this.options = items;},
  append(item) {this.options.push(item);}
};
const inspector = {querySelector: () => widget};
function option(value, label, selected) {return {value, label, selected};}
function t(key) {return key;}
function tf(key, values) {return key + JSON.stringify(values);}
const capability = {
  id: "codex", static_thinking: true, custom_model: true,
  thinking_options: ["low", "high", "ultra"],
  models: [{id: "known-model", reasoning_levels: ["high"]},
           {id: "no-reasoning", reasoning_levels: []}]
};
syncThinking(inspector, capability, process.argv[1]);
console.log(JSON.stringify({
  levels: widget.options.slice(1).map(item => item.value),
  enabled: !widget.disabled, selected: widget.value, title: widget.title
}));
'''
    result = subprocess.run([node, '-e', script, model_id], check=True, text=True, capture_output=True)
    widget = json.loads(result.stdout)
    assert widget['levels'] == expected
    assert widget['enabled'] is enabled
    if model_id == 'custom-model':
        assert widget['selected'] == 'ultra'
        assert widget['title'].startswith('thinkingCustomTitle')
    assert 'const levels = thinkingLevels(capability, value);' in page
