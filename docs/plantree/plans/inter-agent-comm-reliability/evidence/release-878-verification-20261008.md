# v8.7.8 publication verification

Date: 2026-10-08
Status: GitHub and npm published; fresh installation and native FIFO verified.

## Identity and scope

Tag v8.7.8 resolves to `09a8213d1aceed1754d99061492e89855094594d`.
Source and separate metadata PRs #369–#374 are merged. Main at
`9aeddf44a50eb6ff4f01bd264e3b080a987397a2` has the identical candidate tree.
The source tag stays at the exact qualified commit. Git transport failed with
TLS/low-speed errors; API publication preserved the exact local annotated tag
object `01860f88a566e519bae4dbb35160249045c400cb` and target commit.

Includes the Codex adjacent-footer composer and historical busy-text fixes,
earlier Claude pasted-envelope and Codex status-bar repairs, and the separately
reviewed backend and native installation repairs. PR #366 remains excluded.
Issue #356's pasted-but-unsubmitted symptom remains open; the reproduced
pre-send queue stall is a distinct defect.

Release: https://github.com/SeemSeam/claude_codex_bridge/releases/tag/v8.7.8
npm: https://www.npmjs.com/package/@seemseam/ccb/v/8.7.8

## Candidate qualification

- Local complete suite: 7697 passed, 12 skipped, 42 deselected; 72 release
  checks passed. The platform-specific shell test skips locally and is
  exercised on the hosted native runner instead.
- Exact candidate Tests [37704848114](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37704848114):
  success, including full Python lanes, macOS, lifecycle, provider blackbox,
  Rust helpers, install and required gate.
- Real macOS/WSL [37704848135](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37704848135),
  cross-platform [37704851453](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37704851453)
  and isolation [37704848147](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37704848147): success.
- Native build-only [37704847422](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37704847422):
  307 Python tests, 2 Rust tests and installed archive exe/cmd version/help
  smoke passed; publication was skipped. Installation details are recorded
  in [PR #372](https://github.com/SeemSeam/claude_codex_bridge/pull/372).
- A locally built release archive ran real Codex CLI 0.159.2, tmux and managed
  ccbd against a deterministic local Responses endpoint. Two jobs completed
  in FIFO order, each appeared once, drafts remained intact and actual activity
  still blocked sending. This validates native delivery, not remote model
  service availability.
- Intermediate metadata-only qualification runs were cancelled in favor of
  the final synchronized candidate; they are not reported as successful runs.

## Public delivery

Release Artifacts [37706984560](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37706984560),
native package [37706984566](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37706984566),
Sidebar [37706984620](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37706984620)
and npm [37706984609](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37706984609)
all succeeded. Ten assets, published bilingual notes, archive checksum digests
and mobile version/build `8.7.8` / `8070008` were independently checked.
The downloaded public Linux archive bytes independently match its published
SHA256. GitHub latest and npm latest both resolve to v8.7.8 / 8.7.8.

The npm log records successful acceptance followed by a processing delay;
the registry became queryable before installation verification. Direct local
downloads initially failed with socket hang-up and connect-timeout errors.
The successful normal npm install used the configured environment proxy via
`--node-options=--use-env-proxy` and `CCB_PIP_INDEX_URL=https://pypi.org/simple`.
The installer downloaded and verified the public archive, created its managed
Python 3.14.4 environment and provisioned its required dependencies.
CLI version, source commit prefix `09a8213`, Relay imports, exact observer
source equality and the registry signature all passed.

The freshly npm-installed package then repeated the actual Codex/tmux/ccbd
test: both jobs completed in order `[0, 1]`, each native user message appeared
once, the human draft remained intact, actual activity blocked sending and
isolated-project cleanup succeeded. The model endpoint remained the same
deterministic local fixture described under candidate qualification.

Post-merge main Tests [37706872759](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37706872759),
real macOS/WSL [37706872690](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37706872690)
and cross-platform [37706872670](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/37706872670)
also succeeded at the identical merged source tree before this documentation
update. No running main qualification was cancelled to publish this record.

The preceding v8.7.7 publication stopped after its actual installed-archive
smoke failed. Its tag and two Sidebar assets remain unchanged; it is an
incomplete prerelease, and npm 8.7.7 was never published. The install payload
omission was repaired and qualified before cutting v8.7.8.

Raw evidence: `/var/tmp/ccb-878-verification`; earlier partial-publication
evidence: `/var/tmp/ccb-877-verification`. No existing live project or external
Provider state was modified by release qualification.
