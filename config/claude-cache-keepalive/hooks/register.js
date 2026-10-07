import { DEFAULTS, createState, invalidate, observe, eligibility, forkUsage, assessFork, settingsCacheKey } from './policy.js'

const state = createState()
let bridge = []
let timer = null
const children = new Set()

async function rpc($, action, extra = {}) {
  if (!bridge.length) return { allowed: false, reason: 'bridge_unavailable' }
  const response = await $.process.run(bridge, {
    stdin: JSON.stringify({
      action,
      session_id: state.sessionId,
      window_started_at: state.turnStartedAt,
      request_started_at: state.requestStartedAt,
      model: state.model,
      generation: state.generation,
      ...extra,
    }),
    timeoutMs: 5_000,
  })
  if (response.exitCode !== 0) throw new Error('cache bridge failed')
  return JSON.parse(response.stdout)
}

async function note($, reason, extra = {}) {
  state.reason = reason
  try {
    await rpc($, 'report', { reason, ...extra })
  } catch {
    await $.ui.log(`CCB cache keepalive: ${reason} (log bridge unavailable)`, { to: 'debug' })
  }
}

async function helperCacheTtl($) {
  const forceShort = await $.env.get('FORCE_PROMPT_CACHING_5M')
  const disabled = await $.env.get('DISABLE_PROMPT_CACHING')
  if ([forceShort, disabled].some(value => ['1', 'true'].includes(value))) return null
  const explicit = await $.env.get('CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL')
  if (explicit !== undefined) return ['5m', '1h'].includes(explicit) ? explicit : null
  const settings = await $.settings.read()
  if (settings.subagentPromptCacheTtl !== undefined) return ['5m', '1h'].includes(settings.subagentPromptCacheTtl) ? settings.subagentPromptCacheTtl : null
  return ['1', 'true'].includes(await $.env.get('ENABLE_PROMPT_CACHING_1H')) ? '1h' : '5m'
}

async function settleObservation($) {
  const pending = state.pendingObservation
  state.pendingObservation = null
  if (pending.generation !== state.generation) return
  try {
    const evidence = await rpc($, 'observe', { transcript_path: pending.transcriptPath, main_usage: pending.mainUsage })
    if (state.generation === pending.generation) observe(state, evidence)
  } catch {
    invalidate(state, 'usage_probe_failed')
  }
}

async function tick($) {
  if (state.pendingObservation && !state.busy && !state.polling) await settleObservation($)
  const now = await $.clock.now()
  const reason = eligibility(state, now)
  if (reason === 'cache_expired' || reason === 'clock_changed') {
    invalidate(state, reason)
    await note($, reason)
    return
  }
  if (reason !== 'due') {
    if (!['stopped', 'in_flight'].includes(reason) && state.reason !== reason) await note($, reason)
    return
  }
  if (children.size > 0) {
    if (state.reason !== 'subagent_busy') await note($, 'subagent_busy')
    return
  }
  state.polling = true
  const generation = state.generation
  let lease = null
  try {
    const settings = await $.settings.read()
    if (settings.hooks?.SubagentStop?.length) {
      await note($, 'subagent_stop_hooks')
      return
    }
    if (settingsCacheKey(settings) !== state.settingsKey) {
      invalidate(state, 'settings_changed')
      await note($, state.reason)
      return
    }
    const helperTtl = await helperCacheTtl($)
    if (!helperTtl) {
      await note($, 'helper_ttl_unrecognized')
      return
    }
    if (await $.session.model() !== state.model || await $.session.id() !== state.sessionId) {
      invalidate(state, 'session_or_model_changed')
      await note($, state.reason)
      return
    }
    const expectedTokens = state.contextTokens
    lease = await rpc($, 'acquire', { expected_tokens: expectedTokens, helper_ttl: helperTtl })
    if (!lease.allowed) {
      await note($, lease.reason)
      return
    }
    if (state.generation !== generation || state.busy || state.paused || state.stopped || children.size > 0) {
      await rpc($, 'finish', { attempt_id: lease.attempt_id, reason: 'activity_race' })
      return
    }
    const startedAt = await $.clock.now()
    if (startedAt - state.requestStartedAt >= DEFAULTS.expiryMs || startedAt < state.requestStartedAt) {
      await rpc($, 'finish', { attempt_id: lease.attempt_id, reason: 'cache_expired' })
      invalidate(state, 'cache_expired')
      return
    }
    if (state.generation !== generation || state.busy || state.paused || state.stopped || children.size > 0) {
      await rpc($, 'finish', { attempt_id: lease.attempt_id, reason: 'activity_race' })
      return
    }
    state.inFlight = true
    state.attemptCount = lease.attempt_count
    state.outputTokens = lease.output_tokens
    const result = await $.model.fork({ prompt: '[CCB cache keepalive] Reply with a single period only. Do not think, use tools, or perform any work.' })
    const usage = forkUsage(result)
    const outcome = assessFork(result, expectedTokens)
    await rpc($, 'finish', { attempt_id: lease.attempt_id, reason: outcome, usage, started_at: startedAt })
    if (usage) state.outputTokens += usage.output_tokens
    if (state.generation !== generation) return
    if (outcome !== 'cache_hit') {
      state.stopped = true
      invalidate(state, outcome)
      await note($, outcome)
      return
    }
    state.requestStartedAt = startedAt
    await note($, 'cache_hit', { usage, helper_ttl: helperTtl })
  } catch {
    if (lease?.allowed) {
      try { await rpc($, 'finish', { attempt_id: lease.attempt_id, reason: 'keepalive_error' }) } catch {}
    }
    if (state.generation !== generation) return
    state.stopped = true
    invalidate(state, 'keepalive_error')
    await note($, 'keepalive_error')
  } finally {
    state.polling = false
    state.inFlight = false
  }
}

async function bindSession($) {
  timer?.cancel()
  state.sessionId = await $.session.id()
  state.turnStartedAt = 0
  state.model = ''
  state.settingsKey = ''
  state.attemptCount = 0
  state.outputTokens = 0
  children.clear()
  state.pendingObservation = null
  invalidate(state, 'waiting_for_main_request')
  state.busy = false
  state.stopped = false
  timer = $.clock.every(DEFAULTS.pollMs, () => void tick($).catch(() => note($, 'timer_error')))
}

async function start($) {
  const raw = await $.env.get('CCB_CLAUDE_CACHE_BRIDGE')
  try { bridge = JSON.parse(raw || '[]') } catch { bridge = [] }
  await bindSession($)
  await $.command.register({ name: 'ccb-cache', description: 'Inspect or pause CCB delegated-work cache keepalive', argumentHint: '[status|pause|resume]' })
  await note($, 'waiting_for_main_request')
}

async function command($, args) {
  const verb = args.trim().toLowerCase()
  if (verb === 'pause') {
    state.paused = true
    await note($, 'paused')
  } else if (verb === 'resume') {
    if (state.stopped) return { text: 'Stopped after an error or cache miss. Start a new Claude process after reviewing the log.' }
    state.paused = false
  } else if (verb !== '' && verb !== 'status') {
    return { text: 'Usage: /ccb-cache [status|pause|resume]' }
  }
  try {
    const status = await rpc($, 'status')
    if (status.reason === 'bridge_unavailable') return { text: 'CCB cache keepalive is not attached to a running CCB daemon. No model request was made.' }
    const helperTtl = await helperCacheTtl($)
    let phase = eligibility(state, await $.clock.now())
    if (children.size > 0) phase = 'subagent_busy'
    if (phase === 'due' && !status.pending_jobs?.length) phase = 'no_pending_codex'
    if (state.stopped || status.blocked) phase = 'stopped'
    if (state.paused) phase = 'paused'
    return { text: `Cache keepalive: ${phase}; verified main 1h: ${state.verified}; configured helper TTL: ${helperTtl || 'unknown'}; pending Codex jobs: ${status.pending_jobs?.length || 0}; attempts: ${status.attempt_count || 0}/${DEFAULTS.maxAttempts}. No model request was made.` }
  } catch {
    return { text: 'Cache keepalive bridge is unavailable. No model request was made.' }
  }
}

export function register(on) {
  on('session.start', async ($, event, next) => {
    await start($)
    return next(event)
  })
  on('turn.start', async ($, event, next) => {
    if (await $.session.id() !== state.sessionId) await bindSession($)
    state.busy = true
    if (!state.turnStartedAt) {
      const now = await $.clock.now()
      let previous = null
      try { previous = (await rpc($, 'status')).window_started_at } catch {}
      state.turnStartedAt = Number.isFinite(previous) && previous > 0 && previous <= now ? previous : now
    }
    invalidate(state, 'main_busy', { forgetTtl: false })
    return next(event)
  })
  on('turn.step', async function* ($, event, next) {
    if (event.agentId !== undefined) {
      children.add(event.agentId)
      return yield* next(event)
    }
    state.busy = true
    if (state.model && state.model !== event.model) state.verified = false
    state.model = event.model
    state.settingsKey = settingsCacheKey(await $.settings.read())
    state.generation += 1
    state.requestStartedAt = await $.clock.now()
    const generation = state.generation
    const result = yield* next(event)
    if (state.generation === generation) state.mainUsage = result.usage
    return result
  })
  on('classic.Stop', async ($, event, next) => {
    // Claude Code appends the turn's last transcript rows only after Stop hooks
    // return, so the next idle tick verifies this request's usage.
    if (state.mainUsage && state.busy) {
      state.pendingObservation = { transcriptPath: event.transcript_path, mainUsage: state.mainUsage, generation: state.generation }
    }
    return next(event)
  })
  on('turn.complete', async ($, event, next) => {
    if (event.agentId !== undefined) {
      children.delete(event.agentId)
      return next(event)
    }
    state.busy = false
    if (event.reason !== 'answer') invalidate(state, 'main_turn_failed')
    return next(event)
  })
  on('session.compact', async ($, event, next) => {
    if (event.agentId === undefined) invalidate(state, 'context_compacted')
    return next(event)
  })
  on('classic.PostModelSwitch', ($, event, next) => {
    invalidate(state, 'model_changed')
    return next(event)
  })
  on('classic.ConfigChange', ($, event, next) => {
    invalidate(state, 'settings_changed')
    return next(event)
  })
  on('session.end', ($, event, next) => {
    timer?.cancel()
    state.stopped = true
    invalidate(state, 'session_ended')
    return next(event)
  })
  on('command.run', { command: 'ccb-cache' }, ($, event) => command($, event.args))
}
