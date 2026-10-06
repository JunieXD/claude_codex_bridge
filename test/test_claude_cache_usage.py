from __future__ import annotations

import json
from pathlib import Path

import pytest

from provider_backends.claude.cache_usage import transcript_usage, usage_counts


def usage(**overrides):
    return {'input_tokens': 10, 'output_tokens': 1, 'cache_creation_input_tokens': 20000,
            'cache_read_input_tokens': 0, **overrides}


def probe(tmp_path: Path, *, raw=None, model='claude-opus-5-5', **row_overrides):
    root = tmp_path / 'projects'
    root.mkdir(exist_ok=True)
    path = root / 'session.jsonl'
    raw = raw if raw is not None else {**usage(), 'cache_creation': {'ephemeral_1h_input_tokens': 20000, 'ephemeral_5m_input_tokens': 0}}
    row = {'type': 'assistant', 'sessionId': 'session', 'timestamp': '2026-10-05T00:00:01Z',
           'message': {'model': 'claude-opus-5-5', 'usage': raw}, **row_overrides}
    path.write_text(json.dumps(row) + '\n')
    # The mod reports Claude Code's raw usage, which carries no model name.
    return transcript_usage(path, projects_root=root, session_id='session', model=model,
                            expected=usage(), request_started_at=1791158400000)


def test_extracts_only_real_usage_with_ttl(tmp_path):
    evidence = probe(tmp_path)
    assert evidence['reason'] == 'verified_usage'
    assert evidence['usage']['ephemeral_1h_input_tokens'] == 20000
    assert set(evidence) == {'reason', 'usage'}


@pytest.mark.parametrize('raw,reason', [
    (usage(), 'missing_ttl_breakdown'),
    (usage(cache_creation=None), 'missing_ttl_breakdown'),
    (usage(cache_creation_input_tokens=1), 'usage_mismatch'),
    (usage(input_tokens=True), 'missing_usage'),
])
def test_fails_closed_on_unverifiable_usage(tmp_path, raw, reason):
    assert probe(tmp_path, raw=raw)['reason'] == reason


def test_old_or_sidechain_records_cannot_prove_ttl(tmp_path):
    assert probe(tmp_path, timestamp='2026-10-04T00:00:01Z')['reason'] == 'stale_usage'
    assert probe(tmp_path, isSidechain=True)['reason'] == 'missing_usage'
    assert probe(tmp_path, sessionId='other')['reason'] == 'transcript_identity_mismatch'
    assert probe(tmp_path, model='claude-sonnet-5-5')['reason'] == 'missing_usage'


def test_paths_cannot_escape_project_transcripts(tmp_path):
    target = tmp_path / 'elsewhere/session.jsonl'
    target.parent.mkdir()
    target.write_text('secret')
    root = tmp_path / 'projects'
    root.mkdir()
    link = root / 'session.jsonl'
    link.symlink_to(target)
    assert transcript_usage(link, projects_root=root, session_id='session', model='claude-opus-5-5', expected={}, request_started_at=1)['reason'] == 'transcript_outside_session'


@pytest.mark.parametrize('value', [None, {}, usage(input_tokens=-1), usage(output_tokens=1.5)])
def test_usage_is_strict_nonnegative_integer_counts(value):
    assert usage_counts(value) is None
