# JunieXD CCB Fork

This Fork retains upstream's AGPL-3.0 license and uses a live source checkout.
Upstream: https://github.com/SeemSeam/claude_codex_bridge
Maintained Fork: https://github.com/JunieXD/claude_codex_bridge

## Local changes

- Recognize both Codex input markers (`›` and `»`) and the newer two-row footer.
  The footer must retain native terminal styling; plain draft text cannot
  establish an empty input box. Busy states, menus, and editor modes still block
  automatic delivery.
- Read every visible model from the installed Codex model catalog instead of
  filtering by an outdated model-name allowlist. Include GPT-6.1 Sol in the
  fallback catalog.
- Allow manual thinking levels for custom models. Known models keep their
  model-specific levels, while custom selections show a warning to verify
  endpoint support. Compatible existing thinking choices survive model changes.
- In a source installation, `ccb update` fetches only this Fork's `origin`
  branch and uses a fast-forward-only merge. It never installs an official
  release, rewrites global command links, updates providers, or restarts agents.
- Track the previous inherited Claude hooks and permission rules separately
  from managed settings. Changed or removed inherited hooks no longer linger;
  agent-local hooks and permission overrides survive refreshes. Global `deny`
  and `ask` rules remain effective even with agent-local permissions or role
  enforcement. Explicit local allowlists are not widened automatically.
- Canceling a custom-model prompt preserves the previous model and thinking.
  The `startup_args` editor uses a JSON array so spaces, quotes, backslashes,
  and empty arguments round-trip without shell splitting.
- Queue diagnostics and the sidebar distinguish queued, input-held,
  input-unknown, wait-start, running, wait-result, result-queued,
  result-sending, and returned states. Unknown input blocked for at least 30
  seconds adds a sidebar warning and an inspection hint. These are observer
  changes only: no retries, automatic resubmissions, or new clearing behavior.
  Narrow sidebars prioritize compact status tags such as `[input?!]`,
  `[starting]`, `[running]`, `[replying]`, and `[done]` over the provider name.
- Source version checks compare this Fork's current branch ancestry with
  `origin`, distinguishing current, ahead, behind, and diverged revisions.
  They fetch Git metadata but never merge, install, or restart anything.

## Existing managed Claude settings

On the first refresh, CCB creates `.claude/.ccb-settings-projection.json` in
the agent's private home. It records only inherited hooks and permissions,
not provider authentication environment variables. Subsequent refreshes
can replace/remove those inherited entries while retaining local changes.

Legacy managed homes have no reliable record of the source of old hooks.
The first refresh preserves unknown existing hooks rather than guessing and
deleting an agent-local hook. Review any already-stale legacy hooks once;
source changes made after the first refresh are tracked automatically.

## Installation boundary

The development checkout is `/Volumes/Junie2TBSSD/Programs/claude_codex_bridge`.
Cloning, editing, and running tests do not switch an existing npm installation.
Do not run `install.sh install` until switching is explicitly approved and
existing tasks are finished. Later, source installation links commands back to
this checkout; the external disk must remain mounted when those commands run.

Do not upload project `.ccb` data, provider credentials, transcripts, or logs.
They are separate from this source repository and must remain private.

## Updating an installed Fork

Run `ccb update` without a version argument. It requires:

- `origin` points to `JunieXD/claude_codex_bridge` on GitHub.
- The source tree is clean and HEAD is on a branch.
- No merge, rebase, revert, or cherry-pick is in progress.
- No live CCB frontend, daemon, keeper, or sidebar from this source checkout.
- The matching remote branch permits a fast-forward.

The updater never resets or stashes local work. It refuses divergence and
official release-number targets. The runtime check runs before fetching and
again before merging; unrelated npm installations do not block it. Finish
work and exit source CCB projects before updating. Even idle source runtimes
must stop because they can accept new tasks during an update. Open the
projects again afterward to load the changed code.
If a detached project keeps its daemon alive, run `ccb kill` inside that
project only after its tasks have finished, then retry the update.
The `rich` and `mobile` update subcommands retain their upstream behavior.
An npm-owned installation also retains its upstream npm update behavior.

## Incorporating upstream changes

Use `origin` for this Fork and `upstream` for the original repository.
From the maintenance checkout, with a clean working tree:

```sh
git fetch upstream --tags
git merge <reviewed-upstream-commit-or-stable-tag>
```

Resolve conflicts, run the focused tests below, review the diff, then push the
maintenance branch to `origin`. `ccb update` intentionally does not merge
upstream automatically or push developer changes.

```sh
.venv/bin/python -m pytest -q \
  test/test_codex_new_composer.py \
  test/test_input_draft_guard.py \
  test/test_composer_model_independence.py \
  test/test_config_ui_custom_thinking.py \
  test/test_config_ui_editing.py \
  test/test_claude_settings_projection.py \
  test/test_task_presentation.py \
  test/test_config_ui.py \
  test/test_fork_source_update.py \
  test/test_cli_management_update.py \
  test/test_cli_management_version.py
cargo test --locked --manifest-path tools/ccb-agent-sidebar/Cargo.toml
```

Keep changes in focused commits so upstream acceptance can retire individual
patches without losing unrelated fixes. Re-run tests after every merge; a
successful Git merge alone does not establish compatibility.

On macOS, use `TMPDIR=/private/tmp` for Rust socket tests to avoid the native
Unix-domain socket path limit. The upstream sidebar test
`header_buttons_are_right_aligned_and_kill_project` assumes `/bin/true`, which
is unavailable on macOS; skip that test on this host. Linux can run it normally.
