# Issue 356: Codex submission stall investigation

Date: 2026-10-05
Mode: status-update
Status: distinct status-bar pre-send bug repaired and tested locally; reporter's
post-paste symptom not reproduced. Local commit authorized on 2026-10-06;
no deployment or release.

## Objective and boundaries

Reproduce v8.7.5 + Codex 0.156.1 text remaining unsubmitted for over five minutes.
The previously fixed duplicate-resume crash is separate. The guarded sender sets
prompt_sent before terminal I/O, so its normal subsequent poll does not classify
that same prompt as an unsent human draft. No speculative Enter retry or repaste.

Worktree: `/var/tmp/ccb-issue356-fix-20261005`, base `18bf0af07`.
Test project: `/home/bfly/yunwei/test_ccb2/issue356-20261005`.

## Current outcome

Sender-only tests on 0.156.1 and 0.159.2 accepted short/long native pastes; a
0.156.1 real remote turn completed. They did not reproduce the reported
post-paste stall. They are not full CCB mounted-daemon qualification.

Owner subsequently requested judgment and repair of the status-bar problem.
Raw native ANSI and capture replay confirmed a distinct empty-composer pre-send
stall: the middle dot need not be dim, and context text can use a new order.
The repair adds exact context-only support and a structural, dim-placeholder
fallback for empty input. Opaque nonempty drafts remain fail-closed; model names,
FIFO, timer and sender behavior are not changed.

[Repair, regression and native evidence](../evidence/codex-status-bar-20261005.md):
237 focused tests pass; 15 guarded native matrix turns complete without duplicate
markers; draft/menu transitions pass on both clients; pinned real remote turn
returns the requested marker with the guard enabled.

## Remaining questions

- Does the reporter's stuck instance show an unsent guard wait, or an already
  sent prompt with missing native acceptance? Obtain redacted prompt_sent,
  draft_guard_reason and exact stalled screen/cursor evidence.
- Full mounted CCB/tmux and WSL2/Rocky qualification remains separate.
- Arbitrary nonempty custom status-bar layouts are not authorized for automatic
  clearing by this repair; they remain held until manually empty.

No external provider credentials/settings or production panes were changed.
