from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path


USAGE_FIELDS = ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
MAX_TAIL_BYTES = 4 * 1024 * 1024


def usage_counts(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result = {}
    for name in USAGE_FIELDS:
        count = value.get(name)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        result[name] = count
    return result


def transcript_usage(path: Path, *, projects_root: Path, session_id: str, model: str, expected: dict,
                     request_started_at: float) -> dict:
    if path.name != f'{session_id}.jsonl' or not path.resolve().is_relative_to(projects_root.resolve()):
        return {'reason': 'transcript_outside_session'}
    try:
        with path.open('rb') as handle:
            handle.seek(0, 2)
            size = handle.tell()
            offset = max(0, size - MAX_TAIL_BYTES)
            handle.seek(offset)
            tail = handle.read(MAX_TAIL_BYTES)
        rows = tail.splitlines()
        if offset:
            rows = rows[1:]
        for line in reversed(rows):
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(row, dict) or row.get('type') != 'assistant' or row.get('isSidechain'):
                continue
            if row.get('sessionId') != session_id or row.get('isApiErrorMessage'):
                return {'reason': 'transcript_identity_mismatch'}
            message = row.get('message') or {}
            usage = usage_counts(message.get('usage'))
            expected_usage = usage_counts(expected)
            if usage is None or expected_usage is None or message.get('model') != model:
                return {'reason': 'missing_usage'}
            if any(usage[name] != expected_usage[name] for name in USAGE_FIELDS if name != 'output_tokens'):
                return {'reason': 'usage_mismatch'}
            timestamp = datetime.fromisoformat(str(row.get('timestamp', '')).replace('Z', '+00:00')).timestamp() * 1000
            if timestamp < request_started_at:
                return {'reason': 'stale_usage'}
            breakdown = message['usage'].get('cache_creation')
            for name in ('ephemeral_1h_input_tokens', 'ephemeral_5m_input_tokens'):
                count = breakdown.get(name) if isinstance(breakdown, dict) else None
                if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                    if usage['cache_creation_input_tokens']:
                        return {'reason': 'missing_ttl_breakdown'}
                    count = 0
                usage[name] = count
            return {'reason': 'verified_usage', 'usage': usage}
    except (OSError, ValueError, TypeError, AttributeError):
        return {'reason': 'usage_unavailable'}
    return {'reason': 'missing_usage'}
