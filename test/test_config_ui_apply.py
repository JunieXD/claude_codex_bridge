import json
import re
import shutil
import subprocess

import pytest

from cli.services.config_ui import config_ui_asset_path


def run_apply(body):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is needed for Config UI behavioral tests')
    page = config_ui_asset_path().read_text()
    functions = []
    for name in [
        'applyDraft', 'captureDraftSnapshot', 'validateDraft', 'acceptSavedDraft',
        'setConfigApplyStatus', 'pendingAgentRemoval', 'cancelVisualRender',
        'scheduleVisualRender', 'initializeVisualState', 'clone', 'firstLeafPath',
        'extractPercents', 'restorePercents', 'pathKey',
    ]:
        match = re.search(rf'    (?:async )?function {name}\([^\n]*\) \{{\n.*?\n    \}}', page, re.S)
        assert match is not None, name
        functions.append(match.group(0))
    script = '''
let draftText = 'saved-model', draftLoaded = true, draftDirty = true, draftRevision = 0;
let activeConfigDigest = 'before', configApplyInProgress = false;
let visualUndo = [], visualRenderError = null, visualDetached = false;
let visualRenderPromise = null, visualRenderResolve = null, visualRenderTimer = null, visualRenderRevision = 0;
let selectedWindowIndex = 0, selectedPanePath = [], visualNormalizationConfirmed = false;
const nodes = {}, requests = [], apiHandlers = {};
const document = {getElementById(id) {return nodes[id] ||= {textContent: '', className: '', style: {}, dataset: {}};}};
const localStorage = {getItem() {return null;}, setItem() {}};
const window = {location: {protocol: 'http:'}};
const originalSetTimeout = setTimeout;
globalThis.setTimeout = callback => originalSetTimeout(callback, 0);
function t(key) {return key;}
function syncDraftFromEditor() {}
function updateDigestBadge() {}
function renderVisualEditors() {}
function updateCompactPreviews() {}
function setVersion() {}
function syncDocumentFromVisual() {return JSON.parse(JSON.stringify(visualState.document));}
function setActionStatus(message, failed = false) {
  const status = document.getElementById('actionDetail');
  status.textContent = message;
  status.style.color = failed ? 'red' : '';
}
function updateSummary() {document.getElementById('actionDetail').textContent = 'summary';}
function editor(model) {
  return {entry_window: 'main', windows: [{name: 'main', tree: {kind: 'leaf', name: 'codex'}}],
    document: {agents: {codex: {model}}}};
}
function defaultVisualState() {return editor('default');}
let visualState = editor('saved-model');
function deferred() {
  let resolve, reject;
  const promise = new Promise((success, failure) => {resolve = success; reject = failure;});
  return {promise, resolve, reject};
}
let confirmDiff = async () => true;
function savedResult(status = 'saved') {
  return {status, saved: true, digest: 'after', validation: {editor: editor('saved-model')},
    dry_run: {plan_class: 'replace_agent'}, reload: {status: 'published'}};
}
async function apiJson(path, options) {
  const body = JSON.parse(options.body);
  requests.push({path, body});
  if (apiHandlers[path]) return apiHandlers[path](body);
  if (path === '/api/validate') return {agent_names: ['codex'], editor: editor('saved-model')};
  if (path === '/api/preview') return {changed: true, diff: 'reviewed diff'};
  if (path === '/api/render') return {text: body.document.agents.codex.model, editor: {document: body.document}};
  return savedResult();
}
function state() {
  return {requests, draftText, draftDirty, digest: activeConfigDigest, undoEntries: visualUndo.length,
    model: visualState.document.agents.codex.model, busy: configApplyInProgress,
    message: nodes.actionDetail && nodes.actionDetail.textContent, badge: nodes.applyState && nodes.applyState.textContent,
    badgeClass: nodes.applyState && nodes.applyState.className};
}
''' + '\n'.join(functions) + '''
(async () => {
''' + body + '''
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    result = subprocess.run([node, '-e', script], text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_restart_required_response_is_not_reported_as_hot_reload_success():
    result = run_apply('''
apiHandlers['/api/apply'] = () => ({...savedResult('restart_required'), restart_required: true, affected_agents: ['codex']});
await applyDraft('hot_reload');
console.log(JSON.stringify(state()));
''')
    assert result['badge'] == 'restartRequired'
    assert result['badgeClass'] == 'badge warn'
    assert 'configRestartRequired' in result['message']
    assert 'codex' in result['message']
    assert 'configReloaded' not in result['message']
    assert result['draftDirty'] is False
    assert result['busy'] is False


def test_confirmed_hot_reload_is_reported_as_completed():
    result = run_apply('''
apiHandlers['/api/apply'] = () => savedResult('reloaded');
await applyDraft('hot_reload');
console.log(JSON.stringify(state()));
''')
    assert result['message'] == 'configReloaded'
    assert result['badge'] == 'safe'


def test_unconfirmed_hot_reload_is_not_reported_as_success():
    result = run_apply('''
await applyDraft('hot_reload');
console.log(JSON.stringify(state()));
''')
    assert result['badge'] == 'blocked'
    assert 'configReloadUnexpected' in result['message']
    assert result['draftDirty'] is False


@pytest.mark.parametrize('response_error', [False, True])
def test_edit_during_save_preserves_new_draft_and_undo_history(response_error):
    result = run_apply('''
const arrived = deferred(), response = deferred();
apiHandlers['/api/apply'] = () => {arrived.resolve(); return response.promise;};
const applying = applyDraft('save');
await arrived.promise;
draftRevision++; draftDirty = true; draftText = 'new-model';
visualState.document.agents.codex.model = 'new-model';
visualUndo.push('new-edit');
''' + ('''
const error = new Error('daemon unavailable');
error.payload = savedResult('reload_failed');
response.reject(error);
''' if response_error else "response.resolve(savedResult());\n") + '''
await applying;
console.log(JSON.stringify(state()));
''')
    assert result['model'] == result['draftText'] == 'new-model'
    assert result['draftDirty'] is True
    assert result['undoEntries'] == 1
    assert result['digest'] == 'after'
    assert 'configNewerDraft' in result['message']
    assert result['requests'][-1]['body']['text'] == 'saved-model'


def test_edit_during_diff_confirmation_does_not_change_reviewed_candidate():
    result = run_apply('''
confirmDiff = async () => {
  draftRevision++; draftDirty = true; draftText = 'unreviewed-model';
  visualState.document.agents.codex.model = draftText;
  return true;
};
await applyDraft('save');
console.log(JSON.stringify(state()));
''')
    assert result['requests'][-1]['body']['text'] == 'saved-model'
    assert result['model'] == 'unreviewed-model'
    assert result['draftDirty'] is True


def test_duplicate_save_click_does_not_start_second_request():
    result = run_apply('''
const arrived = deferred(), response = deferred();
apiHandlers['/api/apply'] = () => {arrived.resolve(); return response.promise;};
const applying = applyDraft('save');
await arrived.promise;
await applyDraft('hot_reload');
response.resolve(savedResult());
await applying;
console.log(JSON.stringify(state()));
''')
    assert len([request for request in result['requests'] if request['path'] == '/api/apply']) == 1
    assert result['busy'] is False


def test_canceling_diff_does_not_write_config():
    result = run_apply('''
confirmDiff = async () => false;
await applyDraft('save');
console.log(JSON.stringify(state()));
''')
    assert all(request['path'] != '/api/apply' for request in result['requests'])
    assert result['draftDirty'] is True
    assert result['busy'] is False


def test_stale_validation_does_not_replace_newer_toml_draft():
    result = run_apply('''
visualDetached = true;
const arrived = deferred(), response = deferred();
apiHandlers['/api/validate'] = () => {arrived.resolve(); return response.promise;};
const validating = validateDraft();
await arrived.promise;
draftRevision++; draftText = 'new-model';
visualState.document.agents.codex.model = draftText;
response.resolve({agent_names: ['codex'], editor: editor('saved-model')});
await validating;
console.log(JSON.stringify(state()));
''')
    assert result['model'] == result['draftText'] == 'new-model'


def test_rapid_visual_edits_settle_canceled_render_and_save_latest_draft():
    result = run_apply('''
scheduleVisualRender();
const oldRender = visualRenderPromise;
const capturing = captureDraftSnapshot();
visualState.document.agents.codex.model = 'latest-model';
scheduleVisualRender();
const snapshot = await capturing;
console.log(JSON.stringify({snapshot, oldRenderResult: await oldRender}));
''')
    assert result['snapshot']['text'] == 'latest-model'
    assert result['oldRenderResult'] is False


def test_canceled_inflight_visual_render_cannot_overwrite_toml_edit():
    result = run_apply('''
const arrived = deferred(), response = deferred();
apiHandlers['/api/render'] = () => {arrived.resolve(); return response.promise;};
scheduleVisualRender();
const oldRender = visualRenderPromise;
await arrived.promise;
cancelVisualRender(); draftRevision++; draftText = 'typed-toml'; visualDetached = true;
response.resolve({text: 'stale-render', editor: editor('stale-model')});
await new Promise(resolve => originalSetTimeout(resolve, 10));
console.log(JSON.stringify({draftText, canceled: await oldRender}));
''')
    assert result == {'draftText': 'typed-toml', 'canceled': False}
