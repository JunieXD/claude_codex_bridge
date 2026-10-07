import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createState, DEFAULTS, invalidate, observe, eligibility, assessFork, settingsCacheKey } from '../config/claude-cache-keepalive/hooks/policy.js'

const usage = { input_tokens: 10, output_tokens: 1, cache_creation_input_tokens: 500, cache_read_input_tokens: 19500 }
const evidence = { reason: 'verified_usage', usage: { ...usage, ephemeral_1h_input_tokens: 500, ephemeral_5m_input_tokens: 0 } }
const hit = { text: '.', usage: { ...usage, cache_read_input_tokens: 20000, cache_creation_input_tokens: 0 } }
let moduleNumber = 0

function warmState() {
  const state = createState()
  state.mainUsage = usage
  state.requestStartedAt = 1000
  observe(state, evidence)
  return state
}

async function harness(options = {}) {
  const hooks = new Map()
  const imported = await import(`../config/claude-cache-keepalive/hooks/register.js?test=${++moduleNumber}`)
  imported.register((event, matcherOrHandler, handler) => hooks.set(event, handler || matcherOrHandler))
  const calls = []
  const forks = []
  let tickCallback
  let clock = 1000
  let attemptCount = 0
  let sessionId = 'native'
  let model = 'claude-opus-5-5'
  const environment = { CCB_CLAUDE_CACHE_BRIDGE: '["python","bridge"]', CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL: '1h', ...options.env }
  let settings = options.settings || {}
  const engine = {
    clock: { now: async () => clock, every: (interval, callback) => { tickCallback = callback; return { cancel() {} } } },
    env: { get: async name => environment[name] },
    session: { id: async () => sessionId, model: async () => model },
    settings: { read: async () => settings },
    command: { register: async () => {} },
    ui: { log: async () => {} },
    process: { run: async (argv, init) => {
      const request = JSON.parse(init.stdin)
      calls.push(request)
      if (options.onRpc) await options.onRpc(request, fire)
      let response = {}
      if (request.action === 'observe') response = options.evidence || evidence
      if (request.action === 'acquire') response = options.lease || { allowed: true, attempt_id: 'lease', attempt_count: ++attemptCount, output_tokens: 0 }
      if (request.action === 'status') response = { pending_jobs: ['child'], attempt_count: attemptCount, window_started_at: options.previousWindow, ...options.status }
      return { exitCode: 0, stdout: JSON.stringify(response), stderr: '' }
    } },
    model: { fork: async request => { forks.push(request); return options.fork ? options.fork(fire) : hit } },
  }
  async function fire(event, payload = {}) {
    const hook = hooks.get(event)
    return hook?.(engine, payload, async () => ({}))
  }
  async function step(payload = {}) {
    async function* response() { return { usage: { ...usage, model }, stopReason: 'end_turn' } }
    const stream = hooks.get('turn.step')(engine, { model, turnId: 'turn', index: 0, ...payload }, response)
    while (!(await stream.next()).done) {}
  }
  await fire('session.start')
  await fire('turn.start', { turnId: 'turn' })
  await step()
  await fire('classic.Stop', { transcript_path: '/session.jsonl' })
  await fire('turn.complete', { reason: 'answer' })
  return {
    calls, forks, fire, step,
    async tick(age = DEFAULTS.intervalMs) {
      clock = 1000 + age
      tickCallback()
      for (let index = 0; index < 30; index++) await new Promise(resolve => setImmediate(resolve))
    },
    setModel: value => { model = value },
    setSession: value => { sessionId = value },
    setSettings: value => { settings = value },
  }
}

test('TTL needs real one-hour write evidence, never just an env setting', () => {
  const state = createState()
  observe(state, { reason: 'missing_ttl_breakdown' })
  assert.equal(state.verified, false)
  observe(state, evidence)
  assert.equal(state.verified, true)
  observe(state, { reason: 'verified_usage', usage: { ...evidence.usage, ephemeral_5m_input_tokens: 1 } })
  assert.equal(state.verified, false)
})

test('a verified pure hit keeps proof, but an unknown session hit does not establish it', () => {
  const state = warmState()
  const reads = { reason: 'verified_usage', usage: { ...evidence.usage, cache_creation_input_tokens: 0, cache_read_input_tokens: 20000 } }
  observe(state, reads)
  assert.equal(state.verified, true)
  const cold = createState()
  observe(cold, reads)
  assert.equal(cold.verified, false)
})

test('an observation reconciles a previously unavailable native-session window', () => {
  const state = createState()
  state.turnStartedAt = 1000
  observe(state, { ...evidence, window_started_at: 500 })
  assert.equal(state.turnStartedAt, 500)
  assert.equal(state.verified, true)
})

test('time, workload size, activity and budgets fail closed', () => {
  const state = warmState()
  assert.equal(eligibility(state, 1000 + DEFAULTS.intervalMs), 'due')
  assert.equal(eligibility(state, 1000 + DEFAULTS.expiryMs), 'cache_expired')
  assert.equal(eligibility(state, 999), 'clock_changed')
  state.busy = true
  assert.equal(eligibility(state, 1000 + DEFAULTS.intervalMs), 'main_busy')
  state.busy = false
  state.contextTokens = 19999
  assert.equal(eligibility(state, 1000 + DEFAULTS.intervalMs), 'small_context')
  state.contextTokens = 20000
  state.attemptCount = 8
  assert.equal(eligibility(state, 1000 + DEFAULTS.intervalMs), 'budget_exhausted')
  invalidate(state, 'context_compacted')
  assert.equal(state.verified, false)
})

test('usage is verified on the next idle tick, after Stop hooks let Claude Code write the transcript', async () => {
  const rig = await harness()
  assert.equal(rig.calls.filter(call => call.action === 'observe').length, 0)
  await rig.tick(1000)
  assert.equal(rig.calls.filter(call => call.action === 'observe').length, 1)
  assert.equal(rig.forks.length, 0)
  await rig.tick()
  assert.equal(rig.calls.filter(call => call.action === 'observe').length, 1)
  assert.equal(rig.forks.length, 1)
})

test('a new turn before the idle tick discards the pending observation', async () => {
  const rig = await harness()
  await rig.fire('turn.start')
  await rig.tick()
  assert.equal(rig.calls.filter(call => call.action === 'observe').length, 0)
})

test('a real main turn starts a new idle wait with a fresh budget', () => {
  const state = warmState()
  state.attemptCount = 8
  state.outputTokens = 2048
  observe(state, evidence)
  assert.equal(eligibility(state, 1000 + DEFAULTS.intervalMs), 'due')
})

test('bad hits, unknown API errors and unexpectedly large outputs stop warming', () => {
  assert.equal(assessFork(hit, 20010), 'cache_hit')
  assert.equal(assessFork(null, 20010), 'missing_fork_usage')
  assert.equal(assessFork({ ...hit, isAnswered: false, reason: 'api-error' }, 20010), 'fork_failed')
  assert.equal(assessFork({ ...hit, usage: { ...hit.usage, cache_read_input_tokens: 100 } }, 20010), 'cache_miss')
  assert.equal(assessFork({ ...hit, usage: { ...hit.usage, output_tokens: 3000 } }, 20010), 'excessive_output')
})

test('one due tick sends only one isolated fork and reports numeric usage', async () => {
  const rig = await harness()
  await rig.tick()
  await rig.tick()
  assert.equal(rig.forks.length, 1)
  assert.equal(rig.calls.filter(call => call.action === 'acquire').length, 1)
  assert.equal(rig.calls.find(call => call.action === 'finish').reason, 'cache_hit')
  assert.equal(rig.calls.find(call => call.action === 'acquire').helper_ttl, '1h')
})

test('missing main TTL proof produces no paid fork', async () => {
  const rig = await harness({ evidence: { reason: 'missing_ttl_breakdown' } })
  await rig.tick()
  assert.equal(rig.forks.length, 0)
})

test('an unstable main prefix is skipped before acquiring a paid lease', async () => {
  const rig = await harness({ evidence: { reason: 'verified_usage', usage: { ...evidence.usage, cache_read_input_tokens: 0, cache_creation_input_tokens: 20000, ephemeral_1h_input_tokens: 20000 } } })
  await rig.tick()
  assert.equal(rig.forks.length, 0)
  assert.equal(rig.calls.some(call => call.action === 'acquire'), false)
})

test('effective effort or provider changes invalidate a waiting snapshot', async () => {
  for (const settings of [{ effortLevel: 'max' }, { env: { ANTHROPIC_BASE_URL: 'https://example.test' } }]) {
    const rig = await harness()
    rig.setSettings(settings)
    await rig.tick()
    assert.equal(rig.forks.length, 0)
    assert.equal(rig.calls.at(-1).reason, 'settings_changed')
  }
  assert.equal(settingsCacheKey({ env: { ANTHROPIC_AUTH_TOKEN: 'private' } }).includes('private'), false)
})

test('configured SubagentStop hooks prevent unintended fork side effects', async () => {
  const rig = await harness({ settings: { hooks: { SubagentStop: [{ hooks: [{ type: 'command', command: 'unrelated-side-effect' }] }] } } })
  await rig.tick()
  assert.equal(rig.forks.length, 0)
  assert.equal(rig.calls.at(-1).reason, 'subagent_stop_hooks')
})

test('ordinary main turns retain the original delegation window', async () => {
  const rig = await harness()
  await rig.tick(5 * 60_000)
  await rig.fire('turn.start')
  await rig.step()
  await rig.fire('classic.Stop', { transcript_path: '/session.jsonl' })
  await rig.fire('turn.complete', { reason: 'answer' })
  await rig.tick(6 * 60_000)
  const observations = rig.calls.filter(call => call.action === 'observe')
  assert.equal(observations[1].window_started_at, observations[0].window_started_at)
  assert.notEqual(observations[1].request_started_at, observations[0].request_started_at)
})

test('resume retains the native session delegation window without reusing old TTL proof', async () => {
  const rig = await harness({ previousWindow: 500, evidence: { reason: 'missing_ttl_breakdown' } })
  await rig.tick()
  assert.equal(rig.calls.find(call => call.action === 'observe').window_started_at, 500)
  assert.equal(rig.forks.length, 0)
})

test('clear and in-process resume bind the new session before observing another main turn', async () => {
  for (const reason of ['clear', 'resume']) {
    const rig = await harness()
    await rig.fire('session.end', { reason })
    rig.setSession('another-native')
    await rig.fire('turn.start')
    await rig.step()
    await rig.fire('classic.Stop', { transcript_path: '/another-native.jsonl' })
    await rig.fire('turn.complete', { reason: 'answer' })
    await rig.tick()
    assert.equal(rig.forks.length, 1)
    assert.equal(rig.calls.findLast(call => call.action === 'observe').session_id, 'another-native')
  }
})

test('five-minute fork TTL is never silently changed', async () => {
  const rig = await harness({ env: { CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL: '5m' } })
  await rig.tick()
  assert.equal(rig.forks.length, 1)
  assert.equal(rig.calls.find(call => call.action === 'acquire').helper_ttl, '5m')
})

test('unknown TTLs and a forced short or disabled main cache skip all paid requests', async () => {
  for (const env of [{ CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL: 'unknown' }, { FORCE_PROMPT_CACHING_5M: '1' }, { DISABLE_PROMPT_CACHING: 'true' }]) {
    const rig = await harness({ env })
    await rig.tick()
    assert.equal(rig.forks.length, 0)
    assert.equal(rig.calls.at(-1).reason, 'helper_ttl_unrecognized')
  }
})

test('default auxiliary TTL remains five minutes', async () => {
  const rig = await harness({ env: { CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL: undefined } })
  await rig.tick()
  assert.equal(rig.calls.find(call => call.action === 'acquire').helper_ttl, '5m')
  const status = await rig.fire('command.run', { args: 'status' })
  assert.match(status.text, /configured helper TTL: 5m/)
})

test('explicit helper setting works when env does not override it', async () => {
  const rig = await harness({ env: { CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL: undefined }, settings: { subagentPromptCacheTtl: '1h' } })
  await rig.tick()
  assert.equal(rig.forks.length, 1)
})

test('no pending child work means no fork', async () => {
  const rig = await harness({ lease: { allowed: false, reason: 'no_pending_codex' } })
  await rig.tick()
  assert.equal(rig.forks.length, 0)
})

test('sleep past expiry does not rebuild a cold cache', async () => {
  const rig = await harness()
  await rig.tick(DEFAULTS.expiryMs)
  await rig.tick(DEFAULTS.expiryMs + 60000)
  assert.equal(rig.forks.length, 0)
})

test('pause and status never call a model', async () => {
  const rig = await harness()
  await rig.fire('command.run', { args: 'pause' })
  await rig.tick()
  const result = await rig.fire('command.run', { args: 'status' })
  assert.match(result.text, /No model request/)
  assert.equal(rig.forks.length, 0)
})

test('user input racing an acquired lease cancels warming', async () => {
  const rig = await harness({ onRpc: async (request, fire) => { if (request.action === 'acquire') await fire('turn.start') } })
  await rig.tick()
  assert.equal(rig.forks.length, 0)
  assert.equal(rig.calls.find(call => call.action === 'finish').reason, 'activity_race')
})

test('session changes, compaction and settings changes invalidate the snapshot', async () => {
  for (const event of ['session.compact', 'classic.ConfigChange', 'classic.PostModelSwitch', 'session.end']) {
    const rig = await harness()
    await rig.fire(event)
    await rig.tick()
    assert.equal(rig.forks.length, 0)
  }
  const rig = await harness()
  rig.setModel('claude-sonnet-5')
  await rig.tick()
  assert.equal(rig.forks.length, 0)
})

test('a poor hit stops this process, including across further real turns', async () => {
  const rig = await harness({ fork: async () => ({ ...hit, usage: { ...hit.usage, cache_read_input_tokens: 0 } }) })
  await rig.tick()
  await rig.fire('turn.start')
  await rig.step()
  await rig.fire('classic.Stop', { transcript_path: '/session.jsonl' })
  await rig.fire('turn.complete', { reason: 'answer' })
  await rig.tick(2 * DEFAULTS.intervalMs)
  assert.equal(rig.forks.length, 1)
})

test('a real turn during a fork cannot have its snapshot overwritten by that fork', async () => {
  const rig = await harness({ fork: async fire => { await fire('turn.start'); return hit } })
  await rig.tick()
  await rig.tick(2 * DEFAULTS.intervalMs)
  assert.equal(rig.forks.length, 1)
})

test('subagent work starting during a fork prevents another fork until it completes', async () => {
  const rig = await harness({ fork: async () => { await rig.step({ agentId: 'background-child' }); return hit } })
  await rig.tick()
  await rig.tick(2 * DEFAULTS.intervalMs)
  assert.equal(rig.forks.length, 1)
  await rig.fire('turn.complete', { agentId: 'background-child', reason: 'answer' })
  await rig.tick(2 * DEFAULTS.intervalMs)
  assert.equal(rig.forks.length, 2)
})

test('an old failed fork cannot invalidate a new native session', async () => {
  const rig = await harness({ fork: async fire => {
    await fire('session.end', { reason: 'clear' })
    rig.setSession('new-native')
    await fire('turn.start')
    await rig.step()
    await fire('classic.Stop', { transcript_path: '/new-native.jsonl' })
    await fire('turn.complete', { reason: 'answer' })
    throw new Error('old request was aborted')
  } })
  await rig.tick()
  // Not yet due for the new session's request; this tick only verifies its usage.
  await rig.tick(DEFAULTS.intervalMs + 1000)
  const status = await rig.fire('command.run', { args: 'status' })
  assert.doesNotMatch(status.text, /stopped|keepalive_error/)
  assert.equal(rig.calls.findLast(call => call.action === 'observe').session_id, 'new-native')
})

test('skipped contexts and unchanged reasons produce concise diagnostic logs', async () => {
  const rig = await harness({ evidence: { ...evidence, usage: { ...evidence.usage, cache_creation_input_tokens: 10, cache_read_input_tokens: 1000, ephemeral_1h_input_tokens: 10 } } })
  await rig.tick()
  await rig.tick()
  assert.equal(rig.calls.filter(call => call.action === 'report' && call.reason === 'small_context').length, 1)
  const status = await rig.fire('command.run', { args: 'status' })
  assert.match(status.text, /small_context/)
  assert.equal(rig.forks.length, 0)
})

test('a durable daemon stop is visible even if the local snapshot is healthy', async () => {
  const rig = await harness({ status: { blocked: true } })
  const status = await rig.fire('command.run', { args: 'status' })
  assert.match(status.text, /Cache keepalive: stopped/)
  assert.equal(rig.forks.length, 0)
})
