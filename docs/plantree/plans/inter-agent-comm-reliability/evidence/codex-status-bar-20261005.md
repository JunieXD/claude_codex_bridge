# Codex status-bar compatibility repair

Date: 2026-10-05
Status: implemented and tested in isolated worktree; local commit authorized
on 2026-10-06. Not deployed or published.
Related: [issue 356 investigation](../topics/issue-356-submit-stall.md).

## Finding and scope

Pinned official codex-cli 0.156.1 emits a normal status row whose middle-dot
separator is not ANSI dim; a context-only row reads `Context 100% left`.
The old observer requires a dim separator or a different context hint order.
Both native captures therefore return `unknown/composer_layout_unknown` while
the composer is empty. Unknown resets the draft timer; waiting 180 seconds does
not make that empty layout eligible for submission.

Raw terminal output confirms the missing dim attribute independently of pyte:
the placeholder starts with SGR 2, followed by SGR 22 before the footer; the
separator appears after foreground-color reset, without SGR 2. This is a real
UI compatibility gap, not merely a screen-emulator style omission.

This is a pre-send stall. It does not establish the root cause of issue 356's
reported already-pasted-but-unsubmitted text. Earlier sender-only native tests
and one remote turn did not reproduce that symptom.

## Repair and retained boundaries

`lib/provider_execution/draft_observation.py` now accepts the exact new
context-only format (0–100 percent). For otherwise opaque status rows, it
requires the exact fully dim native placeholder at the initial cursor, blank
continuation region, and the existing indented last-row/blank-separator geometry.
This empty-only fallback does not match model names or require separator color.

An opaque footer does not authorize clearing a nonempty draft. Such drafts
remain unknown and held until manually empty or another supported layout is
observed. Busy, menu, special editor mode, binding, timer, FIFO and sender rules
are unchanged. No provider configuration/authentication files were edited.

## Regression evidence

- Initial added tests: **8 failed, 14 passed** before the repair.
- Updated genuine capture replay against original HEAD and patched observer:
  default and context-only rows change from `unknown/composer_layout_unknown`
  to `empty/codex_placeholder`.
- **237 passed** across status-bar observation, model independence, input draft
  guard/FIFO, tmux sender, Codex execution/reply delivery, unified FIFO and
  reply-delivery polling gate tests.
- New tests retain partial/plain-placeholder rejection, cursor/continuation
  checks, draft protection, context format bounds, busy/editor-mode blocking,
  and deferred sender release exactly once without a clear key.

Fixtures: `test/fixtures/composer/codex-status-01561.json`, captured from native
PTY screens using pyte with SGR dim support; equivalent SGR runs and trailing
padding compacted. Raw ANSI output is retained in the external test project.
These are not claimed to be actual tmux capture-pane fixtures.

## Native tests after repair

Test root: `/home/bfly/yunwei/test_ccb2/issue356-20261005`.
Harnesses: `native_probe.py`, `status_probe.py`; before/after result:
`status-before-after.json`.
Production guard and TmuxTextSender run through a PTY transport shim, with
actual native Codex clients; this does not launch a full CCB mounted daemon.

| Artifact directory | Client / scope | Result |
| --- | --- | --- |
| `fixed-context-01561` | 0.156.1, context-only footer | 5 guarded turns accepted/completed |
| `fixed-default-01561` | 0.156.1, model/reasoning + directory | 5 guarded turns accepted/completed |
| `fixed-default-01592` | 0.159.2, model/reasoning + directory | 5 guarded turns accepted/completed |
| `status-boundaries-01561-v2` | 0.156.1, default status | Draft held, manual clear releases, model menu blocks, exit releases; one guarded turn completes |
| `status-boundaries-context-01592-v3` | 0.159.2, context status | Same boundary checks and guarded turn pass |
| `status-remote-01561` | 0.156.1, real remote model, native configuration | Same boundaries; assistant returns `STATUS_BAR_FIXED_OK`; busy observation blocks, completed empty composer releases |

The first three matrices use a local deterministic Responses API: prompt
lengths 60, 3040, 20040, 100040, 60; default 0.5 s paste-to-Enter delay for the
first four and zero delay for the final case. Each matrix records five native
user markers once each, five task_started and five task_complete. HTTP request
counts are not used as execution counts. Native remote success is established
by the assistant marker and return to the empty composer; the fixture-home
rollout reader does not apply to that remote run.

Excluded harness errors: unbracketed `/model` typed together with Enter triggered
native paste-burst suppression; split typing and Enter for menu tests. A startup
trust dialog appearing after an initial placeholder required a readiness recheck.
Neither is treated as a production status-parser failure.

## Limits and rollback

No full daemon/tmux delivery qualification, WSL2/Rocky reproduction, release,
installed-runtime change or universal arbitrary-footer/draft support is claimed.
Reverting the observer patch restores prior behavior without persisted-state
migration. Issue 356's post-paste symptom remains independently unresolved.
