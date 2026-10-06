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
- Installed source entrypoints allow ordinary projects without disabling the
  development-checkout guard. The installed symlink launcher identifies its
  resolved source root; direct, uninstalled source commands and `ccb_test`
  retain their test-project restrictions.
- Explicit Codex `resume` and `fork` startup commands take precedence over
  automatic restoration and run through the native CLI, preserving options
  such as `resume --last` rather than translating them into remote sessions.
- The Config UI distinguishes saved/restart-required settings from a completed
  hot reload. Saving validates and reviews one immutable candidate. Edits made
  while that candidate is being saved retain their draft and undo history;
  repeated save clicks cannot create overlapping writes. Canceled renders
  settle their waiters, and stale render responses cannot overwrite TOML edits.
- Track inherited Claude plugin enablement alongside hooks and permissions.
  Removing an inherited plugin removes its enablement entry, while unrelated
  agent-local plugins and explicit local disablement survive. Hook tracking
  handles individual commands inside matcher groups, so adding a local hook
  does not keep a removed inherited command or duplicate a promoted command.
- Do not replay a mutating daemon request after an ambiguous write or lost
  response. The CLI reports an unknown outcome and directs the caller to inspect
  the queue before retrying. Connection failures before any request is sent and
  read-only observers can still retry. Server rejections are not replayed.
- Failed cancellation delivery leaves the execution and running job tracked,
  reports an actionable error, and permits a later cancellation retry. A clear
  input key alone is not an interrupt. Terminal cancellation confirms transport
  delivery, not termination of detached child processes.
- Profile loads ignore stale responses and preserve edits made while loading.
  Profile saves wait for the latest visual render, capture one draft snapshot,
  and prevent overlapping writes. Unsaved drafts prompt before profile switches
  and browser exit. Saving an inactive profile does not apply the active config.
  Loaded/saved profiles do not falsely trigger unsaved-draft warnings; edits
  made during a save still do.
- Initial task watchers tolerate transient daemon startup/unavailability for up
  to ten seconds, bounded by the requested timeout. They never start a daemon
  and still reject explicitly stopped projects immediately.
- Managed Codex launch parsing shares the option-aware continuation scanner;
  profile/model values named `fork` or `resume` do not become subcommands.
- Worktree enumeration uses NUL-delimited Git output and preserves path spaces,
  line breaks, carriage returns, and non-ASCII names.
- Opt-in Claude cache keepalive uses native tool-less forks only while that
  Claude session is waiting for delegated Codex work. It requires observed
  one-hour main-cache usage, preserves auxiliary TTL settings, logs numeric
  usage, and stops after a miss or unknown cost. See
  [the experimental feature guide](claude-cache-keepalive.md) for limits and
  relay caveats.
- Managed Codex keeps the managed app-server after a restart that resumes with
  `--ask-for-approval`/`--sandbox`: the policy is passed to the app-server as
  config, because `codex --remote ... resume` rejects permission flags. Active
  followups (`ccb followup`) therefore keep working on resumed agents. Only
  `--approve-for-me`, which has no config equivalent, still resumes locally.
- A managed Claude agent shares the user's auto memory for its working
  directory (`~/.claude/projects/<key>/memory`) through a link, so CCB and
  ordinary Claude sessions remember the same things. Existing private memory
  moves over unless a file name conflicts; conflicting memories stay separate.
- A managed Claude agent uses the user's `~/.gitconfig`, `~/.config/gh` and
  `~/.docker` through `GIT_CONFIG_GLOBAL`, `GH_CONFIG_DIR` and `DOCKER_CONFIG`
  instead of empty defaults in its private `HOME`. Explicit caller values win.
- `~/.ccb/memory/<provider>.md` (for example `claude.md`) is user memory that
  only CCB-managed agents of that provider receive, in every project. Use it
  for rules that only make sense under CCB, such as how Claude directs Codex;
  ordinary Claude or Codex sessions never read it. It is projected after the
  provider's own user memory and before project memory. Per project, use
  `.ccb/ccb_memory.md` (all agents) or `.ccb/agents/<agent>/memory.md`.

## Existing managed Claude settings

On the first refresh, CCB creates `.claude/.ccb-settings-projection.json` in
the agent's private home. It records inherited hooks, permissions, and plugin enablement,
not provider authentication environment variables. Subsequent refreshes
can replace/remove those inherited entries while retaining local changes.

Legacy managed homes have no reliable record of the source of old hooks.
The first refresh preserves unknown existing hooks rather than guessing and
deleting an agent-local hook. Review any already-stale legacy hooks once;
source changes made after the first refresh are tracked automatically.
The same legacy ambiguity applies to plugin entries created before plugin
tracking was available. Unknown enablement entries are preserved on first
refresh rather than silently uninstalling an agent-local plugin. Plugin cache
files are not deleted automatically; this change controls enablement only.

## Installation boundary

The development checkout is `/Volumes/Junie2TBSSD/Programs/claude_codex_bridge`.
Cloning, editing, and running tests do not switch an existing npm installation.
Do not run `install.sh install` until switching is explicitly approved and
existing tasks are finished. Later, source installation links commands back to
this checkout; the external disk must remain mounted when those commands run.
Invoke the installed `ccb` link for ordinary projects. The launcher supplies
`CCB_INSTALLED_SOURCE_ROOT` only when reached through an external source-install
symlink, and the Python guard checks that it matches the running checkout.

The local Mac installation switched from npm to this source checkout on
2026-10-05. Global commands, CCB skills, project-managed skill projections,
provider hooks, and native helpers now use the Fork. Managed Codex shell snapshots were rebased
so resumed sessions do not export the removed npm Python or library paths.
User auth, settings, plugin configuration, model/thinking choices, project
configuration, and conversation histories were retained. The private rollback
backup is `/Volumes/Junie2TBSSD/Programs/CCB-migration-backup/2026-10-05`.
Start `ccb` in the existing project to restore its agents; do not reinstall the
upstream npm package over these source links. Future Fork updates use
`ccb update` after all source runtimes have stopped.

Do not upload project `.ccb` data, provider credentials, transcripts, or logs.
They are separate from this source repository and must remain private.

## Updating an installed Fork

### Independent runtime

The preferred local layout separates the development checkout on the external
disk from `/Users/junie/Programs/CCB-runtime` on the internal disk. Runtime files
are real copies, not links to the development disk. Only tracked runtime assets,
an independent Python environment, and three native executables are installed;
Git history, tests, documentation artwork, and Cargo caches stay in development.

Install from a clean, committed checkout with no live CCB source/runtime projects:

```sh
rtk proxy .venv/bin/python scripts/install_fork_runtime.py \
  --runtime-root "$HOME/Programs/CCB-runtime" --bin-dir /opt/homebrew/bin
```

`ccb update` in this installation follows the configured Fork branch, fast-forwards
the external checkout, builds there, verifies a staged runtime, then replaces the
internal runtime. `ccb reinstall` rebuilds it without clearing provider settings.
The development disk is needed only for these operations, not ordinary use.
Version checks query this Fork without reading the development checkout.
Official/npm updates and upstream startup-update prompts are disabled for this
installation, including when its manifest is missing or invalid.

The installer blocks live runtimes, concurrent updates, and new startups during
replacement. Build/validation failures leave the existing installation unchanged;
link-publication failures restore the previous installation and command links.
The previous runtime is kept in `.CCB-runtime.previous` beside the current one,
replacing only an installer-owned backup on the next successful installation.
Authentication, Claude/Codex settings, other plugins, tmux configuration, and
project/session data are not overwritten. Existing project references to the
old source installation must be rebased once during the initial migration;
subsequent updates retain the same internal path.

Native `build-ccb-*` commands rebuild the independent runtime via `ccb reinstall`;
they do not look for Cargo workspaces on the internal disk.

Automatic `ccb uninstall` is deliberately disabled for this layout to avoid
the upstream uninstaller's provider cleanup and unrelated installation defaults.
For removal, stop all projects, remove only command links targeting this runtime,
and delete the runtime plus its installer-owned rollback directory. Leave provider
homes and project `.ccb` data intact.

### Live source installation

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
  test/test_config_ui_apply.py \
  test/test_daemon_request_replay.py \
  test/test_execution_cancel_failure.py \
  test/test_watch_initial_reconnect.py \
  test/test_workspace_git_worktree.py \
  test/test_claude_settings_projection.py \
  test/test_codex_start_cmd_parsing.py \
  test/test_source_runtime_guard.py \
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
Use the same short temporary root for Python Unix-socket integration tests.
