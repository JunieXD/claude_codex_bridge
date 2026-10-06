# v8.7.6 publication verification

Date: 2026-10-06
Status: GitHub and npm published and installed; post-push macOS rerun pending.

## Identity and scope

Tag v8.7.6 resolves to 3b9266cb04975404568e95469bca006ef99a20cd, pushed to main.
Includes Claude pasted-envelope repair (0eebd8fea) and Codex empty-status repair
(0d38d2133), common release metadata (21f021d85) and separate artifact metadata
(3b9266cb0). The artifact metadata passes trusted-base isolation against its
parent. PR 366 is excluded; its repair 3f9346953 is pushed only to
fix/pr366-review-20261006. No existing local installation was upgraded.

Release: https://github.com/SeemSeam/claude_codex_bridge/releases/tag/v8.7.6
npm: @seemseam/ccb@8.7.6, latest=8.7.6.
Registry shasum: 19bf094f89ad07001b517aeb6661332e0e2c5a51.

## Verification

- Exact candidate Tests 37411913053: success, all Linux Python lanes, macOS,
  lifecycle, provider blackbox, install and required gate.
- Exact candidate real-platform 37411920110: macOS and WSL success.
- Exact candidate cross-platform 37411913055: success.
- Local suite: 7618 passed, 11 skipped, 42 deselected, one version metadata
  failure read before the separate metadata commit. Corrected release checks:
  38 passed. Final committed source was validated by the successful CI above.
- Release Artifacts 37413730887, Windows 37413730942, Sidebar 37413730891 and
  npm 37413730962: success. Ten expected assets are present. GitHub-reported
  digests match SHA256SUMS; downloaded mobile manifest hash and 8070006 build
  number verified. Published bilingual notes exactly match the tagged file.
- Fresh exact npm installation succeeded using CCB_PIP_INDEX_URL=https://pypi.org/simple.
  Initial default-index attempt failed finding aiohttp after a download retry;
  the explicit official-index attempt installed all required dependencies.
  CLI reports v8.7.6 / 3b9266c, managed Python 3.14.4 and Relay imports pass.
  npm audit signatures verifies the package's registry signature.
- Main-push real-platform 37413731281 and cross-platform 37413731269: success.

## Post-push failure retained

Repeated Tests 37413731415 failed only in macOS py3.11:
`test_managed_pane_command_ignores_stale_socket_node` exceeded its 15-second
subprocess timeout. Result: 7504 passed, 125 skipped, 42 deselected, 1 failed.
The socket launcher code is unchanged by this release and the same commit's
prepublication run passed. The failed-jobs rerun was cancelled by the documentation push sharing the
main-branch concurrency group. A replacement exact-tag workflow was requested
on v8.7.6, isolated from main pushes; until it finishes this is not an all-green
post-push claim. No tag or package was replaced.

Local raw verification artifacts: /var/tmp/ccb-876-verification.
Issue 356 post-paste symptom remains unconfirmed; publication does not close it.
