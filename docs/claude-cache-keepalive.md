# Claude delegated-work cache keepalive

## Experimental status

This implementation is **experimental and disabled by default**. The preferred
mechanism is a native, tool-less fork that reads the existing verified 1h main
prefix while preserving the user's auxiliary TTL. Do not enable it as a reliable
cost-saving feature on the tested relay yet: the paired retention test below
produced inconsistent results, including a full cold write after a successful
1h fork. Increasing every auxiliary request to 1h is not an established fix.

Claude Code 2.1.285's `model.fork` API accepts only a prompt, not a per-call cache
TTL or output limit. A temporary process-wide environment override would race
with other requests and is deliberately not used. The launcher never writes
`CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL` or changes `subagentPromptCacheTtl`.

On 2026-10-05, two isolated Claude Code 2.1.285 tool-use sessions ran concurrently
against the configured relay. Both main sessions reported real 1h writes and
stable main cache hits. Each issued one native fork at minute 45, followed by an
ordinary main request at minute 63, beyond the original main-request lifetime.
The experimental CLI alone had a 256-token output cap; the production fork API
does not offer one. Numeric evidence is in
[the retention record](claude-cache-retention-2026-10-05.json).

| Configured helper TTL | Fork read / write / output | Main return read / write | Transcript count around fork |
| --- | --- | --- | --- |
| 5m, unchanged | 5,519 / 3 / 3 | 5,522 / 52 | 7 / 7 |
| 1h | 5,533 / 3 / 3 | 0 / 5,588 | 7 / 7 |

Both forks hit nearly the whole prefix, but only one return reused it. Within
each session, the ordinary main tools, system, model, effort and other non-message
request fields stayed unchanged. After normalizing cache markers and equivalent
string/text-block representations, each fork's existing message prefix also
matched the subsequent main prefix. Each lookback had fewer than 20 blocks.
This rules out those obvious local differences, not every internal cache key.
Account routing, upstream cache loss or relay cache handling remain unproven
possibilities; no upstream account identity was captured. One run per condition
does **not** prove that 5m is better than 1h, or establish a universal retention
guarantee. There was no untouched, same-account expiry control.

There is another important discrepancy: the 5m fork's captured outbound markers
were genuinely 5m, but its response reported the three new cache tokens as 1h.
CCB can preserve the local 5m setting, but cannot certify the relay's actual
auxiliary TTL from that setting. Ask the provider about TTL normalization and
sticky account routing before relying on it for savings.

Earlier short tests found that plain-question prefixes could rebuild most of
the cache, even on ordinary main requests. A representative tool-use fork read
5,420 tokens and wrote 45 without changing its transcript. The scheduler therefore
requires the latest ordinary main request itself to have at least a 95%
cache-read ratio before acquiring a lease. A short cache hit alone is not proof
of renewal or later main-request reuse.

The intended request policy is:

- Preserve the user's existing main-request TTL; warm only an observed 1h cache.
- Preserve existing subagent and auxiliary TTLs, including 5m.
- Keep the fork inside the original Claude process, without process-wide TTL mutation.
- Verify the actual cache-read usage and fail closed on a miss or unknown cost.

The code below is an experimental scheduler using Claude Code's `model.fork`
API, not another CLI conversation, terminal keystrokes, or a fabricated task reply.
Claude Code denies the fork's tools and does not write its messages to the main
transcript. A shared cache-safe snapshot alone does not guarantee a cache hit.

## Activation

The Claude launcher loads the bundled `ccb-cache-keepalive` mod only with an
explicit experimental opt-in. Tested
with Claude Code 2.1.285 and its early-access function-hook flag; mods are generally
supported starting at 2.1.287. Older builds, disabled hooks, `--bare`, `--safe-mode`,
or managed policy can prevent loading. The other CCB providers are unaffected.

Global settings, the main cache TTL, and subagent/auxiliary cache TTLs are never
modified. Recognized 5m and 1h helper settings are both supported; an unspecified
helper TTL defaults to 5m. Forced-short or disabled main caching and unknown helper
TTL settings skip warming. No temporary environment mutation is used.

Example agent configuration:

```toml
[agents.claude.env]
CCB_CLAUDE_CACHE_KEEPALIVE = "1"
```

Set this to `"0"` to disable the experimental mod; this is also the default.
The launcher reads this flag in `ccbd`, not in the Claude pane. Exporting it only
in the shell that runs `ccb` is not enough when the keeper/daemon were started
without it: the pane inherits the variable but the mod is silently not loaded.
Check that the Claude process was started with `--plugin-dir` and that
`cache-keepalive.log` appears after the first main turn.
Restart that Claude agent after changing launch environment. To request one-hour
main caching explicitly, configure Claude's `promptCacheTtl = "1h"` setting or
`CLAUDE_CODE_PROMPT_CACHE_TTL=1h` separately; CCB does not silently enable it.

Activation requires **observed API usage**, not just a setting or subscription:

- A genuine main response must report a positive cache write with
  `cache_creation.ephemeral_1h_input_tokens` equal to all newly cached tokens,
  and zero five-minute writes. Mixed, missing or contradictory TTL data disables
  warming. Pure reads retain proof only after a verified write in this process.
- The latest main response must already read at least 95% of its input from
  cache. A recent cold write is not enough; CCB does not pay for an exploratory
  fork against a known unstable main prefix.
- The latest transcript response must belong to this session, match the last
  main step's input counts and model, and postdate that request's start. Only
  usage metadata is extracted; no conversation content leaves the helper.
  Claude Code can run the Stop hook before that row is written, so the helper
  rereads the transcript for up to 1.5 s; the log records the failing reason.
- The helper setting must be recognized, but need not be 1h. Its configured TTL
  is logged separately from the observed main 1h proof; neither a configured
  helper TTL nor one successful fork certifies upstream retention.
- The authoritative CCB dispatcher must still have queued/running, non-cancelled
  Codex `ask` jobs from this Claude agent and from its current delegation window.
  The ownership window is anchored to this native Claude session, not its last
  turn: an ordinary follow-up does not orphan earlier work still in progress.
  A resumed native session recovers that window but must establish fresh TTL
  proof. Both ordinary asks and callback chains work; silent/fire-and-forget
  jobs, replies, unrelated callers and terminal jobs do not qualify.

After `/clear`, resume, compaction or a model/configuration change,
CCB waits for another real main request rather than warming an old snapshot.
Installing the mod cannot retroactively warm an already expired cache.
Restart the managed Claude process after changing or reloading this experimental
plugin. In-process `/clear` and resume rebind the native session and restart its
local timer without reusing old TTL proof.

## Operation and spending safeguards

- Poll locally once per minute; request a fork after 45 minutes without a real
  main request/cache refresh. The clock starts **when the request starts**, not
  when its response finishes.
- Skip busy main turns and observed subagent activity. At 58 minutes, or after
  a backward clock change, treat the snapshot as cold. Never intentionally
  rebuild it after a long sleep or network outage.
- Skip configured `SubagentStop` hooks rather than risk their side effects.
  Other mods can register function hooks outside settings; CCB cannot certify
  the effects of arbitrary third-party mods. Effective model/effort/provider
  setting changes invalidate a waiting snapshot.
- One durable lease prevents overlapping requests. Acquired leases and attempt
  counts survive daemon restarts; an unresolved request fails closed rather than
  being retried. Launch/session fences reject stale processes.
- At most eight acquired attempts per current native session, and stop future
  requests after 2,048 cumulative output tokens. Main turns do not reset those
  budgets. Eligible contexts contain 20,000–1,000,000 input tokens.
- Require at least a 95% cache-read hit and at most 5% uncached/newly cached tail.
  Stop on the first miss, API failure, missing usage or excessive output.
- A pause, new main turn, session/model change or child completion prevents later
  warming. Work finishing just after lease acquisition can still race with one
  already-started isolated request; CCB does not interrupt development work.

**This is not free or a guaranteed monetary cap.** Cached-input reads and output
are billed. A changing relay account/prefix or upstream cache loss can make the
first fork miss and charge a write; CCB can detect this only after the response
and then stops. A successful fork cannot promise that the next ordinary main
request will stay on the same upstream account or reuse that cache.
`model.fork` exposes no per-call output cap, timeout or caller
cancellation handle: the 2,048-token limit stops subsequent calls, not an
already-started one. Claude Code owns its request cancellation/retry behavior.
Any existing `SubagentStop` hooks may also run for the tool-less fork. Review
those hooks if they perform side effects. Relay tariffs and claimed usage are
not independently verified by CCB.

## Status and logs

Inside the CCB Claude pane:

```text
/ccb-cache status
/ccb-cache pause
/ccb-cache resume
```

These local commands make no model request and do not reset spending limits.
After a miss/error, inspect the log before restarting the Claude process.

CCB writes numeric, redacted diagnostics to
`<agent logs directory>/cache-keepalive.log` using its existing rotating log
writer. Observations, reasons, attempts and actual read/write/output counts are
recorded, including the configured helper TTL. Skipped-context/activity reasons
are reported, and identical reason reports are deduplicated. Attempt start/finish events
also appear in each relevant child job's `ccb trace <job_id>` output. The durable
lease/budget/last-observation record is
`<Claude provider runtime directory>/cache-keepalive.json`.

No prompts, replies, credentials, request headers or account details are logged.
If the bridge/daemon cannot be reached, the mod makes no new paid request.
Fork usage is absent from the unchanged main transcript, so transcript-only
tools such as ccusage may not count it. The keepalive log records those numeric
read/write/output counts separately.

## Implementation comparisons

Research on 2026-10-05 found small, recent implementations rather than a broadly
established project for this exact delegated-work case:

- [claude-keepwarm](https://github.com/Delitefully/claude-keepwarm) uses native
  `model.fork`, bounded bumps, and a temporary helper-TTL environment override.
  It explicitly documents early system-role cache-marker misses and the risk
  that concurrent auxiliary requests receive the temporary 1h override. CCB
  does not use that process-wide mutation around a request.
- [cache-tax](https://github.com/karanb192/cache-tax) uses native forks, idle
  windows, cache-readback checks, and persists a small session record. It reports
  one subscription retention test beyond an hour, but warns that helper buckets
  and provider behavior are not universally proven.
- [claude-code-cache-keepalive](https://github.com/demouo/claude-code-cache-keepalive)
  uses a monitor that injects real turns. That changes transcript/context and can
  wake unrelated development work, so it is not used here.
- [Claude-Code-Cache-Keepalive](https://github.com/romantcig/Claude-Code-Cache-Keepalive)
  patches a version-specific Windows binary. Binary patching is not appropriate
  for this maintainable cross-platform CCB feature.
- The [official pre-warming API](https://platform.claude.com/docs/en/build-with-claude/prompt-caching#pre-warming-the-cache)
  accepts `max_tokens: 0`. Replaying an exact captured native request through the
  current relay produced a full cache read, zero writes, and zero output. This
  offers stronger per-call bounds and per-request TTL isolation, but needs a
  capture/transport layer; `model.fork` does not expose its raw tools/system/body.
  Additional direct replays after the failed long test initially read 3,222 and
  wrote roughly 2,300 tokens; repeating the same fork body then read 5,536 with
  zero writes/output. This establishes reuse of that replay's own prefix, not
  a reliable renewal of the original main prefix. The probes reconstructed
  headers rather than preserving every original native header, so a routing
  difference cannot be excluded.
  A proxy or logged-body replay adds privacy, streaming, auth, and lifecycle
  complexity; raw telemetry also redacts thinking, so it is not a safe generic
  request snapshot. No such transport is installed by this experiment.

## Validation

Run the focused Python/Node suite:

```sh
TMPDIR=/private/tmp .venv/bin/python -m pytest -q \
  test/test_claude_cache_usage.py test/test_claude_cache_keepalive.py \
  test/test_claude_cache_mod.py
CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude plugin validate config/claude-cache-keepalive
```

The suite covers proof, stream observation, timing, races, cancellation, queues,
leases, restart persistence, poor hits, budgets, path confinement, in-process
clear/resume, subagents starting during a fork and local controls. A real native
CLI smoke test with a loopback mock API also loaded the production plugin and
bridge, extracted two real transcript records, and persisted their numeric proof
through a real CCB socket without a paid request. Real provider tests should use an isolated session with a small fixed
prefix and a tiny output limit, never the user's live conversation. Compare
main/fork usage and transcript message counts. Short tests validate prefix reuse,
not an hour-long upstream retention guarantee.
