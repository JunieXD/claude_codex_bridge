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
- The matching remote branch permits a fast-forward.

The updater never resets or stashes local work. It refuses divergence and
official release-number targets. Restart idle source runtimes when ready to
load the changed code; already running Python processes keep loaded modules.
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
  test/test_config_ui.py \
  test/test_fork_source_update.py \
  test/test_cli_management_update.py
```

Keep changes in focused commits so upstream acceptance can retire individual
patches without losing unrelated fixes. Re-run tests after every merge; a
successful Git merge alone does not establish compatibility.
