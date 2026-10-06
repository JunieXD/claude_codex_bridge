from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from completion.detectors.session_boundary import SessionBoundaryDetector
from completion.models import CompletionCursor, CompletionItemKind, CompletionRequestContext, CompletionSourceKind, CompletionStatus
from provider_backends.claude.comm_runtime.parsing import structured_event
from provider_backends.claude.execution_runtime.polling import _process_event
from provider_backends.claude.execution_runtime.state_machine_runtime import build_poll_state
from provider_backends.claude.execution_runtime.state_machine_runtime.system_events import has_outer_request_anchor
from provider_execution.base import ProviderSubmission
from provider_hooks.artifacts_runtime.transcript import (
    current_turn_req_id_from_transcript_text,
    extract_outer_req_id,
)

REQ = "job_4fb9cf3a8f91"
NOW = "2026-10-05T14:00:00Z"
PROMPT = f"CCB_REQ_ID: {REQ}\n\nReply OK.\n\nCCB_REPLY_MODE: compact\n"


def envelope(body: str = PROMPT, tag_id: str = "02e1") -> str:
    return f'\n\n<pasted_content id="{tag_id}">\n{body}</pasted_content id="{tag_id}">\n'


@pytest.mark.parametrize("tag_id", ["02e1", "cf5c", "36c7", "a6d9", "a5cf", "A9z", "id-with-dashes", "35420261-1005-4000-8000-000000000001"])
@pytest.mark.parametrize("space", ["", "\n\n", " \t\r\n"])
def test_valid_envelope_both_anchor_consumers(tag_id: str, space: str) -> None:
    text = space + envelope(tag_id=tag_id).strip() + space
    assert has_outer_request_anchor(text, request_anchor=REQ)
    assert extract_outer_req_id(text) == REQ
    assert not has_outer_request_anchor(text, request_anchor=REQ + "9")


INVALID = [
    "prefix " + envelope(), envelope() + "suffix",
    envelope() + envelope(), envelope(envelope()),
    envelope().replace('</pasted_content id="02e1">', '</pasted_content id="02E1">'),
    envelope().replace('</pasted_content id="02e1">', '</pasted_content>'),
    envelope().replace('</pasted_content id="02e1">', ''),
    envelope(tag_id=""), envelope(tag_id="two words"), envelope(tag_id="a\nb"),
    envelope(tag_id='a"b'), envelope(tag_id="a<b"), envelope(tag_id="a>b"),
    envelope("quoted request\n" + PROMPT),
    '```text\n' + envelope() + '\n```',
    envelope().replace('id="02e1"', "id='02e1'"),
    envelope().replace('id="02e1">', 'id="02e1" other="x">'),
]


@pytest.mark.parametrize("text", INVALID)
def test_invalid_envelope_never_becomes_outer_anchor(text: str) -> None:
    assert not has_outer_request_anchor(text, request_anchor=REQ)
    assert extract_outer_req_id(text) is None


@pytest.mark.parametrize("text", [PROMPT, " \n" + PROMPT, PROMPT + envelope("quoted body"), PROMPT.lower(), envelope().replace('<pasted_content id="02e1">', '')])
def test_unwrapped_requests_keep_existing_behavior(text: str) -> None:
    assert has_outer_request_anchor(text, request_anchor=REQ)
    assert extract_outer_req_id(text) == REQ


def records(prompt: str, reply: str = "OK") -> list[dict]:
    return [
        {"type": "user", "uuid": "user", "isSidechain": False, "message": {"role": "user", "content": prompt}},
        {"type": "attachment", "uuid": "attachment", "parentUuid": "user", "attachment": {"type": "context"}},
        {"type": "assistant", "uuid": "answer", "parentUuid": "attachment", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": reply}]}},
        {"type": "system", "subtype": "turn_duration", "uuid": "duration", "parentUuid": "answer", "durationMs": 2951},
    ]


def jsonl(entries: list[dict]) -> str:
    return "\n".join(json.dumps(entry) for entry in entries)


def poll(entries: list[dict], request: str = REQ):
    submission = ProviderSubmission(job_id=request, agent_name="claude1", provider="claude", accepted_at=NOW, ready_at=NOW,
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG, reply="",
        runtime_state={"request_anchor": request, "prompt_sent": True, "state": {}})
    state = build_poll_state(submission)
    for entry in entries:
        event = structured_event(entry)
        if event is not None:
            _process_event(submission, state, event, state={}, now=NOW)
    return state


def test_real_native_283_capture_polling_and_hook_attribution() -> None:
    # Real PTY paste, test-local feature flag, real remote reply and Stop hooks.
    # UUIDs and unrelated metadata are redacted; native text and chain retained.
    fixture = Path(__file__).parent / "fixtures/claude/pasted-envelope-native-2.1.283.jsonl"
    text = fixture.read_text(encoding="utf-8")
    entries = [json.loads(line) for line in text.splitlines()]
    req = "job_7a937526f820"
    state = poll(entries, request=req)
    assert state.anchor_seen and state.reached_turn_boundary
    assert state.reply_buffer == "OK"
    assert current_turn_req_id_from_transcript_text(text, assistant_reply="OK") == req


@pytest.mark.parametrize("blocks", [False, True])
def test_wrapped_turn_reaches_boundary_and_hook_follows_attachment_chain(blocks: bool) -> None:
    entries = records(envelope())
    if blocks:
        entries[0]["message"]["content"] = [{"type": "text", "text": envelope()}]
    state = poll(entries)
    assert state.anchor_seen and state.prompt_activated
    assert state.reached_turn_boundary
    assert state.reply_buffer == "OK"
    assert any(item.kind == CompletionItemKind.TURN_BOUNDARY for item in state.items)
    detector = SessionBoundaryDetector()
    detector.bind(CompletionRequestContext(req_id=REQ, agent_name="claude1", provider="claude", timeout_s=10),
                  CompletionCursor(source_kind=CompletionSourceKind.SESSION_EVENT_LOG, event_seq=0))
    for item in state.items:
        detector.ingest(item)
    assert detector.decision().terminal
    assert detector.decision().status is CompletionStatus.COMPLETED
    assert detector.decision().reply == "OK"
    assert current_turn_req_id_from_transcript_text(jsonl(entries), assistant_reply="OK") == REQ


@pytest.mark.parametrize("kind", ["sidechain", "subagent", "tool_result", "foreign"])
def test_wrapped_non_target_prompt_cannot_activate_poll(kind: str) -> None:
    entries = records(envelope())
    if kind == "sidechain":
        entries[0]["isSidechain"] = True
    elif kind == "subagent":
        entries[0]["agentId"] = "child"
    elif kind == "tool_result":
        entries[0]["message"]["content"] = [{"type": "tool_result", "content": envelope()}]
    else:
        entries[0]["message"]["content"] = envelope(PROMPT.replace(REQ, "job_foreign"))
    state = poll(entries)
    assert not state.anchor_seen
    assert not state.reached_turn_boundary


def test_hook_does_not_borrow_old_wrapped_request_for_new_human_turn() -> None:
    entries = records(envelope()) + [
        {"type": "user", "uuid": "human", "parentUuid": "answer", "message": {"role": "user", "content": "A new unrelated question"}},
        {"type": "assistant", "uuid": "human-answer", "parentUuid": "human", "message": {"role": "assistant", "content": [{"type": "text", "text": "OTHER"}]}},
    ]
    assert current_turn_req_id_from_transcript_text(jsonl(entries), assistant_reply="OTHER") is None


def test_wrapped_queue_requires_activation_not_just_enqueue() -> None:
    enqueue = {"type": "queue-operation", "operation": "enqueue", "uuid": "queued", "content": envelope()}
    state = poll([enqueue] + records("unrelated")[2:])
    assert state.prompt_enqueued and not state.anchor_seen
    activation = {"type": "attachment", "uuid": "activation", "attachment": {"type": "queued_command", "prompt": envelope(), "source_uuid": "queued"}}
    state = poll([enqueue, activation] + records("unrelated")[2:])
    assert state.anchor_seen and state.reached_turn_boundary


@pytest.mark.parametrize("body", [
    PROMPT + '<\\pasted_content id="quoted">example</\\pasted_content id="quoted">',
    PROMPT + 'CCB_REQ_ID: job_quoted\n',
    PROMPT + ("ordinary text\n" * 16000),
])
def test_native_escaped_tags_quoted_ids_and_large_body_keep_outer_identity(body: str) -> None:
    text = envelope(body)
    assert extract_outer_req_id(text) == REQ
    assert has_outer_request_anchor(text, request_anchor=REQ)
    assert not has_outer_request_anchor(text, request_anchor="job_quoted")


def test_malformed_inner_tags_fail_closed_even_after_valid_anchor() -> None:
    for tag in ['<pasted_content', '</pasted_content', '<PASTED_CONTENT id="x">']:
        text = envelope(PROMPT + tag)
        assert not has_outer_request_anchor(text, request_anchor=REQ)
        assert extract_outer_req_id(text) is None


@pytest.mark.parametrize("reply,status", [("OK", "completed"), ("", "incomplete")])
def test_real_finish_hook_writes_wrapped_turn_event(tmp_path: Path, reply: str, status: str) -> None:
    transcript = tmp_path / "session.jsonl"
    entries = records(envelope(), reply) if reply else records(envelope())[:1]
    transcript.write_text(jsonl(entries), encoding="utf-8")
    completion = tmp_path / "completion"
    script = Path(__file__).resolve().parents[1] / "bin/ccb-provider-finish-hook.py"
    result = subprocess.run([sys.executable, str(script), "--provider", "claude", "--completion-dir", str(completion), "--agent-name", "claude1", "--workspace", str(tmp_path)],
        input=json.dumps({"hook_event_name": "Stop", "transcript_path": str(transcript), "session_id": "session", "last_assistant_message": reply}), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    event = json.loads((completion / "events" / f"{REQ}.json").read_text())
    assert event["status"] == status
    assert event["reply"] == reply
