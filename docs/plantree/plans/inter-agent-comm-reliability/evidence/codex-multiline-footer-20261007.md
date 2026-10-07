# Codex fullscreen footer and busy-text repair

Date: 2026-10-07

Status: implemented and verified in the isolated local worktree
`/var/tmp/ccb-codex-footer-20261007`, branch `fix/codex-footer-20261007`.
Owner authorized publication on 2026-10-08. Preparing the source fix for
v8.7.7; existing projects have not been updated. Publication verification
will be recorded separately.

## Reproduced failures and repair

The supplied report describes CCB 8.7.6 and Codex CLI 0.159.2. Both defects
remain in the PR #368 merge baseline:

1. The empty composer has a model/status row immediately followed by the
   shortcut/warnings row. The old parser picks the latter as the boundary,
   requires its preceding row to be blank, and returns
   `unknown / composer_layout_unknown`. Unknown observations hold the FIFO
   head indefinitely; the nonempty-draft 180-second timer does not apply.
2. Assistant bullets containing `(esc to interrupt)` match the old broad
   busy rule. Completed ordinary replies can therefore block the next job.

`lib/provider_execution/draft_observation.py` now finds the start of the
contiguous native footer block. Additional rows must match the native shortcut
hint. Existing editor separation, cursor and placeholder checks still apply;
arbitrary indented continuation and opaque nonempty drafts fail closed.

Busy observation reuses the native Working/Running, tool and Reconnecting
patterns from `provider_pane_status.codex_pane`. Continuations are bounded to
two indented nonblank rows. Later Worked-for evidence supersedes historical
activity, including Codex 0.159.2's indented fullscreen completion summary.
Ordinary interrupt prose does not establish native activity.

## Regression verification

The initial added regression cases failed against the baseline:
`11 failed, 8 passed`. The final related regression run passed:

```sh
uv run --with pytest --with cryptography --with aiohttp --with watchdog \
  pytest -q test/test_codex*.py test/test_input_draft*.py \
  test/test_composer*.py test/test_terminal_runtime_tmux*.py test/test_tmux*.py \
  test/test_unified_message_fifo.py test/test_reply_delivery*.py
```

Result: `585 passed in 19.12s`. After adding the final managed capture,
`test/test_codex_multiline_footer.py` passed all 28 cases. Coverage includes
multiple model labels, single/two-row footers, ordinary answer text, wrapped
activity/completion rows, active tools/reconnection, editor modes, drafts,
unknown continuations, same-head release, no clearing on empty release, and FIFO.

Portable native capture replay is stored in
`test/fixtures/composer/codex-fullscreen-01592.json` (five captures, disposable
project paths redacted). `git diff --check` passes.

## Real CLI and managed queue verification

Test root: `/home/bfly/yunwei/test_ccb2/codex-footer-20261007`.
Tests use the actual Codex CLI 0.159.2, actual tmux capture/sender, and, for the
managed runs, actual candidate ccbd startup, dispatcher and completion reader.
The model endpoint is a deterministic local Responses HTTP fixture, with a
synthetic key and disposable source/managed homes. This qualifies native UI
and delivery behavior, not remote model-service behavior or macOS execution.

`live_probe.py`, successful artifact directory `native-4c05fe64`:

- Fullscreen two-row footer: baseline unknown, candidate empty.
- Human draft and model menu prevent sending; manual exit restores empty.
- Guarded native submission: one user message and one task-complete event.
- A second sender invocation does not duplicate the submission.

`managed_probe.py`, successful artifact directories `managed-76f1d21f` and
final-candidate rerun `managed-641cd806`:

- Validated disposable config and started a real managed Codex pane.
- Empty two-row composer: baseline `composer_layout_unknown`, candidate empty.
- Entered `HUMAN_MANAGED_DRAFT`, submitted two jobs: accepted/queued,
  clear-attempted false, draft preserved.
- Manually cleared the draft with Ctrl-U. Both jobs completed in submission
  order; each job appeared exactly once as a native user message.
- Observed `provider_busy` during actual Working; no second turn overlapped.
- Both native replies contained `解释提示 (esc to interrupt)`. The second
  queued task still ran. The completed capture is `provider_busy` under the
  baseline and `empty / codex_placeholder` under the candidate.
- Cleaned up both managed projects through their own `ccb kill`; successful
  standalone probe also stopped its owned tmux server and HTTP fixture.

Inline `--no-alt-screen` produces a single footer and does not reproduce this
defect; the fullscreen captures are required evidence. Early harness failures
(schema/backend choice, named-session lookup, transient startup, receipt shape,
and fixture-key forwarding) are not counted as passing runs. Managed fixture
inheritance is explicitly rooted through `CCB_SOURCE_HOME`; a custom CCB-prefixed
key was replaced with the standard synthetic `OPENAI_API_KEY` forwarding path.

## Next action

Land the shared repair as an independently reviewed source change, then
qualify and publish v8.7.7 under the owner's 2026-10-08 authorization.
Track package/source identity and CI in the release verification record.
