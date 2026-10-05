from __future__ import annotations

import json


def _copy(value):
    return json.loads(json.dumps(value))


def _fingerprint(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _hook_group_key(group) -> str:
    return _fingerprint({key: value for key, value in group.items() if key != 'hooks'})


def _local_hook_groups(groups, baseline):
    inherited_groups = {_fingerprint(group) for group in baseline}
    inherited_hooks = {}
    for group in baseline:
        if isinstance(group, dict) and isinstance(group.get('hooks'), list):
            inherited_hooks.setdefault(_hook_group_key(group), set()).update(
                _fingerprint(hook) for hook in group['hooks']
            )
    local = []
    for group in groups:
        if _fingerprint(group) in inherited_groups:
            continue
        if isinstance(group, dict) and isinstance(group.get('hooks'), list):
            inherited = inherited_hooks.get(_hook_group_key(group), set())
            hooks = [hook for hook in group['hooks'] if _fingerprint(hook) not in inherited]
            if not hooks:
                continue
            group = {**group, 'hooks': hooks}
        local.append(group)
    return local


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
        if event in source and not isinstance(source[event], list):
            continue
        combined = source.get(event, [])
        local = _local_hook_groups(
            groups, [*(old_groups if isinstance(old_groups, list) else []), *combined],
        )
        seen = {_fingerprint(group) for group in combined}
        for group in local:
            fingerprint = _fingerprint(group)
            if fingerprint not in seen:
                combined.append(group)
                seen.add(fingerprint)
        if combined:
            source[event] = combined
    return source


def merge_projected_plugins(projected, existing, previous=None) -> dict:
    source = _copy(projected) if isinstance(projected, dict) else {}
    managed = existing if isinstance(existing, dict) else {}
    baseline = previous if isinstance(previous, dict) else {}
    for name, value in managed.items():
        if name not in source and (name not in baseline or value != baseline[name]):
            source[name] = _copy(value)
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
