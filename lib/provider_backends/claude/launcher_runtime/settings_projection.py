from __future__ import annotations

import json


def _copy(value):
    return json.loads(json.dumps(value))


def _fingerprint(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def merge_projected_hooks(projected, existing, previous=None) -> dict:
    source = _copy(projected) if isinstance(projected, dict) else {}
    managed = _copy(existing) if isinstance(existing, dict) else {}
    baseline = previous if isinstance(previous, dict) else {}
    for event, groups in managed.items():
        old_groups = baseline.get(event)
        if not isinstance(groups, list):
            if groups != old_groups and event not in source:
                source[event] = groups
            continue
        inherited = {_fingerprint(group) for group in old_groups} if isinstance(old_groups, list) else set()
        local = [group for group in groups if _fingerprint(group) not in inherited]
        if event in source and not isinstance(source[event], list):
            continue
        combined = source.get(event, [])
        seen = {_fingerprint(group) for group in combined}
        for group in local:
            fingerprint = _fingerprint(group)
            if fingerprint not in seen:
                combined.append(group)
                seen.add(fingerprint)
        if combined:
            source[event] = combined
    return source


def merge_projected_permissions(projected, existing, previous=None) -> dict:
    source = _copy(projected) if isinstance(projected, dict) else {}
    managed = _copy(existing) if isinstance(existing, dict) else {}
    baseline = previous if isinstance(previous, dict) else {}
    for key, value in managed.items():
        old_value = baseline.get(key)
        if key == 'allow' and isinstance(value, list) and (
            not isinstance(old_value, list) or not all(item in value for item in old_value)
        ):
            source[key] = value
        elif key in {'allow', 'deny', 'ask'} and isinstance(value, list):
            inherited = old_value if isinstance(old_value, list) else []
            local = [item for item in value if item not in inherited]
            combined = source.get(key, [])
            if not isinstance(combined, list):
                combined = []
            source[key] = list(dict.fromkeys([*combined, *local]))
        elif key not in baseline or value != old_value:
            source[key] = value
    return source
