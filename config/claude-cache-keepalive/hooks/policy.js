export const DEFAULTS = Object.freeze({
  intervalMs: 45 * 60_000,
  expiryMs: 58 * 60_000,
  pollMs: 60_000,
  minContextTokens: 20_000,
  maxContextTokens: 1_000_000,
  maxAttempts: 8,
  maxOutputTokens: 2_048,
  minReadRatio: 0.95,
})

export function createState() {
  return {
    generation: 0,
    busy: false,
    polling: false,
    inFlight: false,
    paused: false,
    stopped: false,
    reason: 'waiting_for_main_request',
    sessionId: '',
    turnStartedAt: 0,
    requestStartedAt: 0,
    model: '',
    mainUsage: null,
    verified: false,
    contextTokens: 0,
    cacheReadTokens: 0,
    attemptCount: 0,
    outputTokens: 0,
    settingsKey: '',
  }
}

export function invalidate(state, reason, { forgetTtl = true } = {}) {
  state.generation += 1
  state.mainUsage = null
  state.requestStartedAt = 0
  state.contextTokens = 0
  state.cacheReadTokens = 0
  state.reason = reason
  if (forgetTtl) state.verified = false
}

export function observe(state, evidence) {
  state.attemptCount = 0
  state.outputTokens = 0
  if (Number.isFinite(evidence?.window_started_at) && evidence.window_started_at > 0) state.turnStartedAt = evidence.window_started_at
  const usage = evidence?.usage
  if (!usage || evidence.reason !== 'verified_usage') {
    state.verified = false
    state.reason = evidence?.reason || 'missing_usage'
    return
  }
  if (usage.cache_creation_input_tokens > 0) {
    state.verified = usage.ephemeral_1h_input_tokens === usage.cache_creation_input_tokens && usage.ephemeral_5m_input_tokens === 0
  } else if (usage.cache_read_input_tokens <= 0) {
    state.verified = false
  }
  state.contextTokens = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
  state.cacheReadTokens = usage.cache_read_input_tokens
  state.reason = state.verified ? 'verified_1h' : 'unverified_1h'
}

export function eligibility(state, now, config = DEFAULTS) {
  if (state.stopped) return 'stopped'
  if (state.paused) return 'paused'
  if (state.inFlight || state.polling) return 'in_flight'
  if (state.busy) return 'main_busy'
  if (!state.verified || !state.mainUsage || !state.requestStartedAt) return state.reason
  if (state.contextTokens < config.minContextTokens) return 'small_context'
  if (state.contextTokens > config.maxContextTokens) return 'large_context'
  if (state.attemptCount >= config.maxAttempts || state.outputTokens >= config.maxOutputTokens) return 'budget_exhausted'
  const age = now - state.requestStartedAt
  if (age < 0) return 'clock_changed'
  if (age >= config.expiryMs) return 'cache_expired'
  if (state.cacheReadTokens < state.contextTokens * config.minReadRatio) return 'main_prefix_unstable'
  if (age < config.intervalMs) return 'not_due'
  return 'due'
}

export function settingsCacheKey(settings) {
  const environment = settings.env || {}
  return JSON.stringify([
    settings.model, settings.effortLevel, settings.fastMode,
    settings.promptCacheTtl, settings.subagentPromptCacheTtl,
    environment.ANTHROPIC_BASE_URL, environment.CLAUDE_CODE_PROMPT_CACHE_TTL,
    environment.CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL,
    environment.FORCE_PROMPT_CACHING_5M, environment.DISABLE_PROMPT_CACHING,
  ])
}

export function forkUsage(result) {
  const usage = result?.usage
  if (!usage) return null
  const names = ['input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens']
  if (names.some(name => !Number.isSafeInteger(usage[name]) || usage[name] < 0)) return null
  return Object.fromEntries(names.map(name => [name, usage[name]]))
}

export function assessFork(result, expectedTokens, config = DEFAULTS) {
  const usage = forkUsage(result)
  if (!usage) return 'missing_fork_usage'
  if (result.isAnswered === false && result.reason !== 'empty-reply') return 'fork_failed'
  if (usage.cache_read_input_tokens < expectedTokens * config.minReadRatio) return 'cache_miss'
  if (usage.cache_creation_input_tokens + usage.input_tokens > expectedTokens * (1 - config.minReadRatio)) return 'expensive_tail'
  if (usage.output_tokens > config.maxOutputTokens) return 'excessive_output'
  return 'cache_hit'
}
