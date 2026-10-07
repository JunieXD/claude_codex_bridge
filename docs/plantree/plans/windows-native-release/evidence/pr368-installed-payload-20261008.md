# PR #368 installed entrypoint repair

Date: 2026-10-08
Status: candidate; native runner qualification pending.

## Native release failure

v8.7.7 candidate `5875fbc0d03f7e2aa6677ec39a33b9427dbe1ef7` passed
Linux/macOS qualification, but native Windows release run
[37703500204](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37703500204)
failed during archive installation:

```text
Installed ccb.exe smoke check failed: ccb Windows launcher:
Python entrypoint is missing:
<install-prefix>\platforms\windows\ccb.py
```

PR #368 changed the native launcher to use the Windows-owned entrypoint.
The archive includes it, but the installer's selective copy list omitted
`platforms/windows`. Source review and Linux mocks therefore missed an
installed-layout failure that the native release smoke caught.

The partial v8.7.7 publication was stopped: npm remains v8.7.6, only sidebar
assets were published, and the release is marked prerelease with an explicit
bilingual incomplete-publication notice. The original tag is unchanged.
The owner-authorized replacement release will be v8.7.8.

## Repair and acceptance

- Copy the Windows-owned payload into the managed installation, creating its
  parent before copying and retaining replacement semantics on update.
- Point installed .cmd/.bat launchers at the same native entrypoint as ccb.exe.
- Exercise the actual PowerShell copy block twice, checking entrypoint/support
  files, paths containing spaces and replacement without directory nesting.
- Add a build-only invocation to the existing Windows release workflow.
  Candidate source refs require publish=false. Build jobs have read-only
  repository access; only the guarded publication job can write release assets.
- Native archive smoke verifies both ccb.exe and ccb.cmd version/help paths.

Local verification: 59 targeted tests passed; the one PowerShell execution
test is skipped on this Linux host. YAML/publication-guard checks and
git diff --check pass. Native installed-archive qualification must pass before
the replacement tag is published. The patch does not modify shared runtime,
global version/package metadata, external Provider configuration or the
isolation checker.
