# Codex fullscreen footer and busy-text repair

Date: 2026-10-07

Status: implemented and verified in the isolated local worktree
`/var/tmp/ccb-codex-footer-20261007`, branch `fix/codex-footer-20261007`.
Owner authorized publication on 2026-10-08. Preparing the source fix for
v8.7.7; existing projects have not been updated. Publication verification
will be recorded separately.

## Windows PR prerequisite

[PR #368](https://github.com/SeemSeam/claude_codex_bridge/pull/368) was already
merged when the final audit checked GitHub:

- Base: `0b81137330e52ea0cdd8fc7d54172550e921b7f6`.
- Reviewed head: `4772deb6f604d4a5923ae01bd92a2c7b6a0b958d`.
- Merge: `d25152eda64698f1de856f1d345c2f1ce4da34c1`.
- Merge timestamp: `2026-10-07T13:38:35Z`.

The checker exported from the trusted base passed with `scope=windows`:

```sh
python3 /tmp/ccb-pr368-policy/platforms/windows/tools/check_pr_isolation.py \
  --repo-root /tmp/ccb-pr368-review --base 0b811373 --head 4772deb
```

The GitHub Windows isolation check also passed. PR-directed tests passed
`306 passed, 1 skipped`; the merge tree equals the reviewed head tree.
Changes remain Windows-owned; no shared version/release metadata is bundled.
This Linux host does not establish a new native Windows/WezTerm functional
qualification. The shared Codex repair below is separate from that Windows PR.

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

Land the shared repair separately from Windows functional changes, then
qualify and publish v8.7.7 under the owner's 2026-10-08 authorization.
Track package/source identity and CI in the release verification record.
