from __future__ import annotations

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))

from ccbd.socket_client import CcbdClient
from provider_backends.claude.cache_usage import settled_transcript_usage
from storage.paths import PathLayout


def main() -> int:
    try:
        raw = sys.stdin.read(16_385)
        if len(raw) > 16_384:
            raise ValueError('request too large')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('invalid request')
        project_root = os.environ['CCB_CALLER_PROJECT_ROOT']
        payload.update({
            'actor': os.environ['CCB_CALLER_ACTOR'],
            'launch_id': os.environ['CCB_SESSION_ID'],
            'runtime_dir': os.environ['CCB_CALLER_RUNTIME_DIR'],
        })
        if payload.get('action') == 'observe':
            projects_root = os.environ.get('CLAUDE_PROJECTS_ROOT')
            if not projects_root:
                config = os.environ.get('CLAUDE_CONFIG_DIR') or str(Path.home() / '.claude')
                projects_root = str(Path(config) / 'projects')
            payload['evidence'] = settled_transcript_usage(
                Path(payload.pop('transcript_path')),
                projects_root=Path(projects_root),
                session_id=str(payload['session_id']),
                model=payload.get('model'),
                expected=payload.pop('main_usage'),
                request_started_at=float(payload['request_started_at']),
            )
        layout = PathLayout(Path(project_root))
        result = CcbdClient(layout.ccbd_socket_path, timeout_s=3).request('claude_cache', payload)
        print(json.dumps(result, separators=(',', ':')))
        return 0
    except Exception:
        print(json.dumps({'allowed': False, 'reason': 'bridge_unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
