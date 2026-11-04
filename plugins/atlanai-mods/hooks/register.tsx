// Registry Mods: a Claude Code mod for the atlanai plugin's `atlan` server.
//
// tool.check (mcp__call_tool): registry reads run without a prompt; writes ask, naming the
//   write; archive, approve and move always ask, naming what depends on the artifact, and the
//   Atlan pane shows the same as a card while the question waits.
// tool.call (the three gateway tools): moves session_id, rationale and objective out of
//   `arguments`, reuses the session's open audit session, refuses a call with no rationale,
//   learns tool annotations and the session id from results, and records what each call read,
//   changed or published for the pane and the publish band.
// ui.render: each gateway call's transcript row reads as one line of what it did and found; the
//   `atlan` pane lists the artifacts this session touched; the band above the prompt follows a
//   skill publish to its end; the `atlan-skills` pane picks a skill to load.
// session.start: checks the connection without opening an audit session, and puts a problem,
//   never a success, on the status line. /atlan reports it; /atlan search, skills and pane.
// prompt.submit: clears a finished publish band, and searches the registry's skills with the
//   person's prompt: one that clearly fits is named beside the prompt, as context the model reads.
//   It rides prompt.submit because Claude Code's built-in cc-plugin-sec-default, seated for Team
//   and Enterprise organizations and on machines with managed settings, passes installed plugins
//   over on prompt.compose and prompt.context. A slow or failed search adds nothing.
//
// What the session learned lives in this module, one copy per session. It is not kept in the
// host's plugin state, which needs a types contract named in plugin.json: a key the desktop app
// does not read and warns about on install. A hot reload forgets it, and the next registry call
// learns it again. A change redraws with `$.ui.invalidate('ui.render')`.
//
// The host reads `on(...)` and `$.noun.method(...)` from source, so both are spelled literally.

import type { EngineInterface, Register } from 'claude-code'

import {
  CALL_TOOL,
  capped,
  runnerCap,
  FIX,
  GATEWAY_TOOL,
  probeServers,
  artifactOf,
  classify,
  dependentsOf,
  describeBlastRadius,
  describeWrite,
  diagnose,
  findSessionId,
  harvestHints,
  isStaleSession,
  repairRails,
  resultText,
  retryNote,
  retryOf,
  searchRows,
  serverKeyOf,
  summaryOf,
  unwrap,
  type AtlanHealth,
  type CallInput,
  type HintTable,
} from './registry'
import {
  SETTLED,
  cardOf,
  clip,
  describeCall,
  followPublish,
  mergeTouches,
  settlePublishes,
  skillRows,
  startPublishes,
  summarizeResult,
  touchesOf,
  type Card,
  type Publish,
  type SkillRow,
  type Touch,
} from './view'
import { HINT_MS, hintLine, hintQuery, isHintable, isInstalled, skillMatches, type HintPrompt, type SkillMatch } from './hint'

const HINTS_KEY = 'hints'
const LOOKUP_MS = 6000
/** The most a permission prompt waits on the blast-radius reads, a retry included, and the most
 * it sleeps before that retry whatever `retry_after_seconds` says. */
const BLAST_BUDGET_MS = 8000
const BLAST_RETRY_MAX_S = 3
const PROBE_MS = 15000
const RETRY_MS = 20000
const PANE = 'atlan'
const PICKER = 'atlan-skills'
const DASHBOARD = 'https://app.atlan.com'
const PUBLISH = 'extension__skill__create'
const GROUP_ROWS = 6
const HINT_RATIONALE = "Looking for a published skill in the organization's registry that fits the user's request."
/** Searches kept per session, for a prompt sent again. */
const HINT_CACHE = 50
/** Hint searches that may time out in a row before the hint pauses, and how long it pauses (also
 * how long a degraded connection keeps it off). */
const HINT_TIMEOUTS = 2
const HINT_PAUSE_MS = 5 * 60 * 1000

const RATIONALE_HELP =
  'Atlan needs a one-sentence `rationale` for every mcp__call_tool call, beside `name` and `arguments` (not inside `arguments`). Add it and call again.'
const NO_APPROVER = 'Nobody can confirm it here (as under claude -p), so the Atlan mod refused it. Run it in a session where you can answer the prompt.'
const GUARD_FAILED = 'The Atlan registry guard could not check this call, so it needs your confirmation.'
const USAGE =
  'Usage: /atlan (connection status), /atlan search <words>, /atlan skills [words] (pick a skill to load) or /atlan pane (what this session touched)'

type Dollar = EngineInterface
type Lookup = { text: string; isError: boolean }
type Picker = { query: string; state: 'idle' | 'searching' | 'done' | 'failed'; rows: SkillRow[]; total: number; note: string }

/** What this session has learned and shows. */
type Memory = {
  /** The audit session the model's own calls opened, reused for every later call. */
  session: string
  /** A session the mod's own lookups opened while the model had none. Never given to the model's
   * calls: they open their own, under the objective the model states. */
  housekeeping: string
  health?: AtlanHealth
  /** The calls this mod's permission check saw, by tool_use_id, so a write that ran without it
   * shows on the status line. */
  checked: Set<string>
  /** Registry writes that ran while another plugin answered the permission check first. */
  unguarded: number
  /** Registry reads the same way: they asked again or ran on another plugin's verdict, not this
   * mod's allow. Counted for /atlan only; a read changes nothing, so it raises no warning. */
  unguardedReads: number
  /** Artifacts this session read or changed, newest first. */
  touches: Touch[]
  /** Confirmation cards for calls waiting on the person, by tool_use_id. */
  cards: Map<string, Card>
  /** Skill publishes this turn, for the band. */
  publishes: Publish[]
  /** The server key of the gateway the model last called, when it is not one the mod probes
   * anyway (a gateway added as `atlan-prod`). */
  seenServer?: string
  isBandHidden: boolean
  /** The pane opens by itself once a session, at its first write or question. */
  hasOpenedPane: boolean
  picker: Picker
  /** The skill hint's state, for the conversation `hintSession` names: a /clear starts a new one. */
  hint: { session: string; named: Set<string>; searches: Map<string, Promise<SkillMatch[]>> }
  /** The hint's circuit breaker, kept across conversations: it is about the gateway, not the
   * prompt. `timeouts` in a row, the time the pause ends, and whether a search is still out. */
  hintBreaker: { timeouts: number; pausedUntil: number; isSearching: boolean }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** Annotations learned from the gateway, shared by every session on this machine. */
async function loadHints($: Dollar): Promise<HintTable> {
  const stored = await $.store.get(HINTS_KEY)
  return isRecord(stored) ? (stored as HintTable) : {}
}

async function saveHints($: Dollar, learned: HintTable): Promise<void> {
  if (Object.keys(learned).length === 0) return
  const current = await loadHints($)
  await $.store.set(HINTS_KEY, { ...current, ...learned })
}

const STATUS: Record<AtlanHealth['state'], string | undefined> = {
  ready: undefined,
  unknown: undefined,
  'sign-in': 'Atlan: sign in with /mcp',
  missing: 'Atlan: not connected, run /atlan',
  unreachable: 'Atlan: offline, run /atlan',
  degraded: 'Atlan: degraded, retrying reads',
  error: 'Atlan: error, run /atlan',
}

const UNGUARDED =
  'Another plugin answered the permission check for an Atlan registry write before Registry Mods could, so the write did not get its confirmation.'

/** The status line: the connection's problem, then a skipped guard, or nothing when all is well. */
function paint($: Dollar, mem: Memory): void {
  const parts = [mem.health ? STATUS[mem.health.state] : undefined, mem.unguarded > 0 ? 'Atlan: write guard skipped, run /atlan' : undefined]
  const line = parts.filter(Boolean).join(' · ')
  $.ui.status(line || undefined)
}

function setHealth($: Dollar, mem: Memory, health: AtlanHealth): void {
  mem.health = health
  paint($, mem)
}

function redraw($: Dollar): void {
  $.ui.invalidate('ui.render')
}

/** Opens the Atlan pane the first time this session writes or asks; the person may close it. */
function offerPane($: Dollar, mem: Memory, always = false): void {
  if (mem.hasOpenedPane && !always) return
  mem.hasOpenedPane = true
  void $.ui.open({ id: PANE, title: 'Atlan' }).catch(() => undefined)
}

/**
 * A gateway call made by the mod itself: under the model's audit session when one is open, else
 * under the mod's own. A session the mod opens stays the mod's, so the model's next call does not
 * inherit an objective it never stated.
 */
async function lookup($: Dollar, mem: Memory, server: string, name: string, args: Record<string, unknown>, rationale: string): Promise<Lookup> {
  const session = mem.session || mem.housekeeping
  const rails = session
    ? { session_id: session }
    : { objective: 'Claude Code with the Atlan registry: the Registry Mods plugin checks registry changes and connection status for the user.' }
  const result = await $.mcp.call(server, 'mcp__call_tool', { name, arguments: args, rationale, ...rails })
  const text = result.content.map(block => (isRecord(block) && typeof block.text === 'string' ? block.text : '')).join('\n')
  const opened = findSessionId(text)
  if (opened && opened !== session && !mem.session) mem.housekeeping = opened
  if (result.isError && session && session === mem.housekeeping && isStaleSession(text)) mem.housekeeping = ''
  return { text, isError: result.isError }
}

/** How a bounded wait ended: the work's value, the clock, or the work's own failure. */
type Outcome<T> = { kind: 'ok'; value: T } | { kind: 'timeout' } | { kind: 'error'; message: string }

/** Settles once `ms` pass, so a slow registry never holds a prompt, and keeps a failure apart from
 * a timeout: a refused sign-in is not an offline server. */
async function within<T>($: Dollar, ms: number, work: Promise<T>): Promise<Outcome<T>> {
  const stop = new AbortController()
  const timer = $.clock.sleep(ms, { signal: stop.signal }).then(
    (): Outcome<T> => ({ kind: 'timeout' }),
    (): Outcome<T> => ({ kind: 'timeout' }),
  )
  const settled = work.then(
    (value): Outcome<T> => ({ kind: 'ok', value }),
    (error: unknown): Outcome<T> => ({ kind: 'error', message: error instanceof Error ? error.message : String(error) }),
  )
  try {
    return await Promise.race([settled, timer])
  } finally {
    stop.abort()
  }
}

/** The value of a bounded wait that worked, else `undefined`. */
const valueOf = <T,>(outcome: Outcome<T>): T | undefined => (outcome.kind === 'ok' ? outcome.value : undefined)

/** A deadline for a read and its retry together, and the longest wait before the retry. */
type Budget = { totalMs: number; maxWaitSeconds: number }

/** A read the mod makes for itself, tried again once after a transient failure. With a budget, the
 * wait is capped and the retry runs only when it can finish inside the deadline, so a gateway
 * asking for `retry_after_seconds: 60` never holds a permission prompt for a minute. */
async function lookupWithRetry($: Dollar, mem: Memory, server: string, name: string, args: Record<string, unknown>, rationale: string, ms: number, budget?: Budget): Promise<Outcome<Lookup>> {
  const started = await $.clock.now()
  const first = await within($, budget ? Math.min(ms, budget.totalMs) : ms, lookup($, mem, server, name, args, rationale))
  const transient = first.kind === 'ok' ? first.value.isError && retryOf(first.value.text) : first.kind === 'error' && retryOf(first.message)
  if (!transient) return first
  if (!budget) {
    await $.clock.sleep(transient.afterSeconds * 1000)
    return within($, ms, lookup($, mem, server, name, args, rationale))
  }
  const waitMs = Math.min(transient.afterSeconds, budget.maxWaitSeconds) * 1000
  const left = budget.totalMs - ((await $.clock.now()) - started) - waitMs
  if (left < 1000) return first
  await $.clock.sleep(waitMs)
  return within($, Math.min(ms, left), lookup($, mem, server, name, args, rationale))
}

const BLAST: Budget = { totalMs: BLAST_BUDGET_MS, maxWaitSeconds: BLAST_RETRY_MAX_S }

/** The permission prompt's text for an archive, move or approve, and the pane's card for it. */
async function blastRadius($: Dollar, mem: Memory, callId: string, tool: string, name: string, input: Record<string, unknown>): Promise<{ reason: string; card?: Card }> {
  const id = artifactOf(input.arguments)
  if (!id) return { reason: describeWrite(name, input.arguments) }
  const server = tool.slice('mcp__'.length, tool.lastIndexOf('__mcp__'))
  const why = 'Checking what depends on this artifact before the user confirms the change.'
  const [related, artifact] = await Promise.all([
    lookupWithRetry($, mem, server, 'registry__get_related_artifacts', { artifact_id: id }, why, LOOKUP_MS, BLAST).then(valueOf),
    lookupWithRetry($, mem, server, 'registry__get_artifact', { artifact_id: id }, why, LOOKUP_MS, BLAST).then(valueOf),
  ])
  const summary = artifact && !artifact.isError ? summaryOf(artifact.text) : undefined
  const dependents = related && !related.isError ? dependentsOf(related.text) : undefined
  return { reason: describeBlastRadius(name, id, summary, dependents), card: cardOf(callId, name, id, summary, dependents) }
}

/** Checks the connection with `mcp__describe_tools`, which opens no audit session. */
async function probe($: Dollar, mem: Memory): Promise<AtlanHealth> {
  let detail = 'no Atlan server answered'
  let state: AtlanHealth['state'] = 'missing'
  for (const server of probeServers(mem.seenServer)) {
    try {
      const outcome = await within($, PROBE_MS, $.mcp.call(server, 'mcp__describe_tools', { servers: ['registry'], detail: 'summary' }))
      if (outcome.kind === 'timeout') {
        state = 'unreachable'
        detail = `${server} did not answer within ${PROBE_MS / 1000} seconds`
        continue
      }
      if (outcome.kind === 'error') throw new Error(outcome.message)
      const result = outcome.value
      const text = result.content.map(block => (isRecord(block) && typeof block.text === 'string' ? block.text : '')).join('\n')
      if (result.isError) {
        state = diagnose(text)
        detail = `${server} refused the check`
        continue
      }
      const learned = harvestHints(text)
      if (Object.keys(learned).length === 0) {
        state = 'error'
        detail = `${server} answered, but listed no registry tools`
        continue
      }
      await saveHints($, learned)
      return { state: 'ready', server, detail: `${Object.keys(learned).length} registry tools`, checkedAt: await $.clock.now() }
    } catch (error) {
      const found = diagnose(error instanceof Error ? error.message : String(error))
      // A server that is missing under one name may be there under the next: keep the worse state.
      if (state === 'missing' || found !== 'missing') state = found
      detail = `${server}: ${found}`
    }
  }
  return { state, server: '', detail, checkedAt: await $.clock.now() }
}

function report(mem: Memory, health: AtlanHealth, confirmWrites: boolean): string {
  const session = mem.session
  const lines = [
    health.state === 'ready' ? `Atlan: connected through ${health.server} (${health.detail}).` : `Atlan: ${health.state} (${health.detail}).`,
    FIX[health.state] ?? FIX.error,
  ]
  if (health.state === 'ready') {
    lines.push(
      confirmWrites
        ? 'Writes ask first. Archive, approve and move always ask, with what depends on the artifact, and are refused where nobody can answer.'
        : 'Writes follow your permission rules. Archive, approve and move always ask, and are refused where nobody can answer.',
    )
    lines.push(session ? 'An audit session is open and is reused for every call.' : 'No audit session yet: the first registry call opens one.')
  }
  if (mem.unguarded > 0) lines.push(`${UNGUARDED} Writes this session: ${mem.unguarded}. Check which plugins answer permission prompts with /plugin.`)
  if (mem.unguardedReads > 0)
    lines.push(`Another plugin answered the permission check for ${mem.unguardedReads} registry read${mem.unguardedReads === 1 ? '' : 's'} before this mod could, so reads may ask. Check which plugins answer permission prompts with /plugin.`)
  return lines.join('\n')
}

/** Runs a registry search through the server that answered last, or each one in turn. */
async function searchRegistry($: Dollar, mem: Memory, args: Record<string, unknown>, rationale: string): Promise<Lookup | undefined> {
  const health = mem.health
  const servers = health?.state === 'ready' && health.server ? [health.server] : probeServers(mem.seenServer)
  for (const server of servers) {
    const found = valueOf(await lookupWithRetry($, mem, server, 'registry__search_artifacts', args, rationale, PROBE_MS))
    if (found) return found
  }
  return undefined
}

async function search($: Dollar, mem: Memory, words: string): Promise<string> {
  if (!words) return USAGE
  const found = await searchRegistry($, mem, { query: words, semantic: true, limit: 10 }, 'The user searched the registry with the /atlan search command.')
  if (!found) return `Atlan did not answer. ${FIX.unreachable}`
  if (found.isError) return `Atlan refused the search. ${FIX[diagnose(found.text)] ?? FIX.error}`
  const rows = searchRows(found.text)
  if (!rows || rows.rows.length === 0) return `Nothing in Atlan matches "${words}".`
  const more = rows.total > rows.rows.length ? `, showing ${rows.rows.length}` : ''
  return [`${rows.total} found for "${words}"${more}:`, ...rows.rows.map(row => `  ${row}`)].join('\n')
}

/** Fills the skill picker with the skills that match `words`. */
async function pickerSearch($: Dollar, mem: Memory, words: string): Promise<void> {
  const query = words.trim()
  if (!query) {
    mem.picker = { query, state: 'idle', rows: [], total: 0, note: '' }
    redraw($)
    return
  }
  mem.picker = { query, state: 'searching', rows: [], total: 0, note: '' }
  redraw($)
  const found = await searchRegistry($, mem, { query, kind: 'skill', semantic: true, limit: 15 }, 'The user searched skills with the /atlan skills picker.')
  if (mem.picker.query !== query) return
  if (!found || found.isError) {
    const why = (found ? FIX[diagnose(found.text)] : FIX.unreachable) ?? ''
    mem.picker = { query, state: 'failed', rows: [], total: 0, note: why }
  } else {
    const rows = skillRows(found.text)
    mem.picker = { query, state: 'done', rows: rows?.rows ?? [], total: rows?.total ?? 0, note: '' }
  }
  redraw($)
}

/** Connection states in which the hint searches nothing: the search could only fail. */
const NO_HINT: ReadonlySet<AtlanHealth['state']> = new Set(['sign-in', 'missing', 'unreachable'])

/** The first answer a registry search gets from `servers`, tried in turn. */
async function firstAnswer($: Dollar, mem: Memory, servers: readonly string[], args: Record<string, unknown>, rationale: string): Promise<Lookup> {
  let last: Lookup = { text: '', isError: true }
  for (const server of servers) {
    last = await lookup($, mem, server, 'registry__search_artifacts', args, rationale).catch(() => last)
    if (!last.isError) return last
  }
  return last
}

/**
 * The context lines that name registry skills which clearly fit the prompt, or none. Searched once
 * per prompt text a conversation, through the server the connection check found (or each one in
 * turn while the check has not settled, as on a session's first prompt); signed out, missing or
 * offline, nothing is searched. Each skill is named once a conversation, and never when a skill
 * of that name is installed here. Never throws.
 */
async function skillHints($: Dollar, mem: Memory, prompt: HintPrompt): Promise<string[]> {
  if (!isHintable(prompt)) return []
  const health = mem.health
  if (health && NO_HINT.has(health.state)) return []
  const breaker = mem.hintBreaker
  const now = await $.clock.now()
  // A degraded gateway, or two searches in a row that outlasted the wait, pause the hint for a
  // while: each search would only add its wait to the prompt and load a struggling gateway.
  if (health?.state === 'degraded' && now - health.checkedAt < HINT_PAUSE_MS) return []
  if (now < breaker.pausedUntil) return []
  const servers = health?.state === 'ready' && health.server ? [health.server] : probeServers(mem.seenServer)
  const session = await $.session.id()
  if (session !== mem.hint.session) mem.hint = { session, named: new Set(), searches: new Map() }
  const hint = mem.hint
  const query = hintQuery(prompt.text)
  let pending = hint.searches.get(query)
  if (!pending) {
    // One search out at a time: a new prompt while one is still running goes without a hint.
    if (breaker.isSearching) return []
    breaker.isSearching = true
    pending = firstAnswer($, mem, servers, { query, kind: 'skill', semantic: true, limit: 3 }, HINT_RATIONALE).finally(() => {
      breaker.isSearching = false
    }).then(
      found => {
        if (found.isError) hint.searches.delete(query)
        return found.isError ? [] : skillMatches(found.text)
      },
      () => {
        hint.searches.delete(query)
        return []
      },
    )
    hint.searches.set(query, pending)
    if (hint.searches.size > HINT_CACHE) hint.searches.delete(hint.searches.keys().next().value as string)
  }
  // A search that outlasts the wait still finishes, and answers the same prompt sent again.
  const outcome = await within($, HINT_MS, pending)
  if (outcome.kind === 'timeout') {
    breaker.timeouts += 1
    if (breaker.timeouts >= HINT_TIMEOUTS) {
      breaker.timeouts = 0
      breaker.pausedUntil = now + HINT_PAUSE_MS
    }
  } else {
    breaker.timeouts = 0
  }
  const matches = (valueOf(outcome) ?? []).filter(match => !hint.named.has(match.id))
  if (matches.length === 0) return []
  const installed = valueOf(await within($, HINT_MS, $.command.list()))?.map(command => command.name) ?? []
  const fresh = matches.filter(match => !isInstalled(match.name, installed))
  for (const match of fresh) hint.named.add(match.id)
  return fresh.map(hintLine)
}

/** Puts the chosen skill in the prompt as the atlanai plugin's command, for the person to send. */
async function pickSkill($: Dollar, row: SkillRow): Promise<void> {
  await $.prompt.fill({ text: `/atlanai:use-skill ${row.id} ` })
  await $.ui.close({ id: PICKER })
}

const MARK: Record<Publish['state'], string> = {
  sending: '…',
  scanning: '…',
  published: '✓',
  blocked: '✗',
  unchanged: '=',
  failed: '✗',
}

export const register: Register = (on, options) => {
  const confirmWrites = options.confirm_writes !== false
  const suggestSkills = options.suggest_skills !== false
  const mem: Memory = {
    session: '',
    housekeeping: '',
    checked: new Set(),
    unguarded: 0,
    unguardedReads: 0,
    touches: [],
    cards: new Map(),
    publishes: [],
    isBandHidden: false,
    hasOpenedPane: false,
    picker: { query: '', state: 'idle', rows: [], total: 0, note: '' },
    hint: { session: '', named: new Set(), searches: new Map() },
    hintBreaker: { timeouts: 0, pausedUntil: 0, isSearching: false },
  }

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'atlan',
      description: 'Atlan connection status; /atlan search <words>, /atlan skills to pick a skill, /atlan pane for what this session touched',
      argumentHint: '[search <words> | skills [words] | pane]',
      immediate: true,
    })
    const check = async (): Promise<void> => {
      const health = await probe($, mem)
      // MCP servers can still be connecting at start: report a problem only if it outlasts a retry.
      if (health.state === 'ready') setHealth($, mem, health)
      else
        $.clock.after(RETRY_MS, async () => {
          setHealth($, mem, await probe($, mem))
        })
    }
    void check().catch(() => undefined)
    return next(e)
  })

  on('command.run', { command: 'atlan' }, async ($, e) => {
    const [sub = '', ...rest] = e.args.trim().split(/\s+/)
    const words = rest.join(' ').trim()
    if (sub === 'search') return { text: await search($, mem, words) }
    if (sub === 'skills') {
      const opened = await $.ui.open({ id: PICKER, title: 'Atlan skills', focus: true, closeOnEscape: true })
      void pickerSearch($, mem, words)
      return { text: opened.isPlaced ? 'Pick a skill in the Atlan skills pane.' : `The skill picker could not open here. Try /atlan search ${words || '<words>'}.` }
    }
    if (sub === 'pane') {
      mem.hasOpenedPane = true
      const opened = await $.ui.open({ id: PANE, title: 'Atlan' })
      return { text: opened.isPlaced ? 'The Atlan pane lists what this session read and changed.' : 'The Atlan pane opens once the window is wide enough.' }
    }
    if (sub !== '' && sub !== 'status' && sub !== 'doctor') return { text: USAGE }
    const health = await probe($, mem)
    setHealth($, mem, health)
    return { text: report(mem, health, confirmWrites) }
  })

  on('prompt.submit', async ($, e, next) => {
    // A new request starts with a clean band: finished publishes leave it, running ones stay.
    if (mem.publishes.length > 0 && mem.publishes.every(one => SETTLED.has(one.state))) {
      mem.publishes = []
      mem.isBandHidden = false
      redraw($)
    }
    const hints = suggestSkills ? await skillHints($, mem, e).catch(() => []) : []
    return hints.length > 0 ? next({ ...e, context: [...(e.context ?? []), ...hints] }) : next(e)
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: GATEWAY_TOOL }, async ($, e, next) => {
    const call = e as unknown as CallInput & { tool: string; tool_use_id?: string }
    const isCallTool = CALL_TOOL.test(call.tool)
    mem.seenServer = serverKeyOf(call.tool) ?? mem.seenServer
    const known = mem.session
    const { input } = repairRails(call, known, isCallTool)
    if (isCallTool && (typeof input.rationale !== 'string' || input.rationale.trim() === '')) {
      return { deny: RATIONALE_HELP }
    }
    const name = typeof input.name === 'string' ? input.name : ''

    let started: Publish[] = []
    if (isCallTool && name === PUBLISH) {
      started = startPublishes(input.arguments)
      const names = new Set(started.map(one => one.name))
      mem.publishes = [...mem.publishes.filter(one => !names.has(one.name)), ...started]
      mem.isBandHidden = false
      redraw($)
    }

    let ran: Awaited<ReturnType<typeof next>>
    try {
      ran = await next({ ...e, ...input } as typeof e)
    } finally {
      // The question, if one was put, has been answered either way.
      if (call.tool_use_id && mem.cards.delete(call.tool_use_id)) redraw($)
    }
    if (ran.deny !== undefined) {
      if (started.length > 0) {
        mem.publishes = mem.publishes.map(one => (started.some(s => s.name === one.name) ? { ...one, state: 'failed' as const, text: `${one.name}: not sent` } : one))
        redraw($)
      }
      return ran
    }

    const text = resultText(ran)
    const isError = ran.isError === true
    const opened = findSessionId(text)
    if (opened && opened !== known) mem.session = opened
    else if (isError && known && input.session_id === known && isStaleSession(text)) mem.session = ''

    // The call that really ran, and what it does: a nested call_tool is judged by what it wraps.
    const inner = isCallTool ? unwrap(name, input.arguments) : { name: call.tool.slice(call.tool.lastIndexOf('__mcp__') + 2), args: undefined }
    const effect = classify(inner.name, await loadHints($))
    if (isCallTool && call.tool_use_id) {
      const wasChecked = mem.checked.delete(call.tool_use_id)
      if (!wasChecked && (effect === 'write' || effect === 'destructive')) {
        mem.unguarded += 1
        if (mem.unguarded === 1) $.ui.toast(`Atlan: ${UNGUARDED}`)
        paint($, mem)
      } else if (!wasChecked && effect === 'read') {
        mem.unguardedReads += 1
      }
    }

    if (!isCallTool) await saveHints($, harvestHints(text))

    if (isCallTool) {
      if (started.length > 0) {
        const settled = settlePublishes(started, text, isError)
        mem.publishes = mem.publishes.map(one => settled.find(s => s.name === one.name) ?? one)
        for (const one of settled) {
          if (one.state === 'published' || one.state === 'blocked') $.ui.toast(`Atlan: ${one.text}`)
        }
      } else if (mem.publishes.length > 0) {
        const before = mem.publishes
        mem.publishes = followPublish(before, name, text, isError)
        mem.publishes.forEach((one, i) => {
          if (one.state !== before[i]?.state && (one.state === 'published' || one.state === 'blocked')) $.ui.toast(`Atlan: ${one.text}`)
        })
      }
      const touched = touchesOf(name, input.arguments, text, isError)
      if (touched.length > 0) {
        mem.touches = mergeTouches(mem.touches, touched)
        if (touched.some(one => one.action !== 'read')) offerPane($, mem)
      }
      redraw($)
    }

    const health = mem.health
    const retry = isError ? retryOf(text) : undefined
    if (!isError) mem.hintBreaker.pausedUntil = 0
    if (!isError && health?.state !== 'ready') {
      setHealth($, mem, { state: 'ready', server: health?.server ?? '', detail: 'a registry call succeeded', checkedAt: await $.clock.now() })
    } else if (isError && diagnose(text) === 'sign-in') {
      setHealth($, mem, { state: 'sign-in', server: health?.server ?? '', detail: 'a registry call was refused', checkedAt: await $.clock.now() })
    } else if (retry) {
      setHealth($, mem, { state: 'degraded', server: health?.server ?? '', detail: 'a registry call failed with a temporary error', checkedAt: await $.clock.now() })
    }
    // A transient failure tells the model how to recover: repeat a read after a wait, read back
    // before repeating a write. The engine never runs a call twice for a hook. A lost skill create
    // is left to atlanai's publish helper and atlan-author-skills' read-back: it takes no
    // idempotency_key, so this note's advice would be wrong for it, and two notes would disagree.
    if (retry && ran.result !== undefined && !(isCallTool && inner.name === PUBLISH)) {
      return { ...ran, context: [...(ran.context ?? []), retryNote(effect === 'read' ? 'read' : 'write', retry)] }
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('tool.check', { tool: CALL_TOOL }, async ($, e, next) => {
    if (e.tool_use_id) {
      mem.checked.add(e.tool_use_id)
      // Bounded: a call whose run never reached tool.call leaves its id behind.
      if (mem.checked.size > 200) mem.checked.delete(mem.checked.values().next().value as string)
    }
    const verdict = await next(e)
    if (verdict.decision === 'deny') return verdict

    const outer = isRecord(e.input) ? e.input : {}
    const inner = unwrap(outer.name, outer.arguments)
    const name = inner.name
    const input: Record<string, unknown> = { ...outer, name, arguments: inner.args }
    const effect = capped(classify(name, await loadHints($)), runnerCap(e.tool), name)

    if (effect === 'unknown') return verdict
    if (effect === 'read') {
      // An organization's ceiling on the connector stands above this mod.
      if (e.ceiling === 'ask' || e.ceiling === 'deny') return verdict
      return { decision: 'allow', reason: `Atlan: ${name} only reads the registry.` }
    }
    if (effect === 'write') {
      // Nobody to ask (claude -p, the Agent SDK): the session's own rules decide a write.
      if (!confirmWrites || e.approver === undefined) return verdict
      return { decision: 'ask', reason: describeWrite(name, input.arguments) }
    }
    // Archive, approve and move: asked wherever someone can answer, refused where nobody can.
    const { reason, card } = await blastRadius($, mem, e.tool_use_id ?? '', e.tool, name, input)
    if (e.approver === undefined) return { decision: 'deny', reason: `${reason} ${NO_APPROVER}` }
    if (card && e.tool_use_id && e.approver !== undefined) {
      mem.cards.set(e.tool_use_id, card)
      offerPane($, mem, true)
      redraw($)
    }
    return { decision: 'ask', reason }
  }).catch(() => ({ decision: 'ask', reason: GUARD_FAILED }))

  // 1. Each gateway call's row: what it did, in one line. ctrl+o (expanded) shows the engine's own.
  on('ui.render', { component: 'ToolUse' }, ($, e, next) => {
    if (e.component !== 'ToolUse' || !GATEWAY_TOOL.test(e.props.tool) || e.props.isExpanded === true) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const status = e.props.isInterrupted ? ' (interrupted)' : e.props.isRunning ? ' …' : ''
    return (
      <Box>
        <Text bold>Atlan</Text>
        <Text>
          {' · '}
          {describeCall(e.props.tool, e.props.input)}
        </Text>
        <Text dimColor>{status}</Text>
      </Box>
    )
  })

  on('ui.render', { component: 'ToolResult' }, ($, e, next) => {
    if (e.component !== 'ToolResult' || !GATEWAY_TOOL.test(e.props.tool) || e.props.isExpanded === true) return next(e)
    const { Text } = $.ui.resolve(e)
    const summary = summarizeResult(resultText({ result: e.props.output }), e.props.isErrored)
    return <Text dimColor={!e.props.isErrored}>{`  ⎿ ${summary}`}</Text>
  })

  // A run of Atlan calls the transcript folds into one count line ("Called atlan 4 times"): one
  // line per call instead, the latest few, so the run says what it did. A mixed run stays folded.
  on('ui.render', { component: 'ToolGroup' }, ($, e, next) => {
    if (e.component !== 'ToolGroup' || e.props.isExpanded || e.props.calls.length === 0) return next(e)
    // The model often loads the Atlan tools with ToolSearch first, in the same run: leave it out.
    const calls = e.props.calls.filter(call => call.tool !== 'ToolSearch')
    if (calls.length === 0 || !calls.every(call => GATEWAY_TOOL.test(call.tool))) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const shown = calls.slice(-GROUP_ROWS)
    const earlier = calls.length - shown.length
    return (
      <Box flexDirection="column">
        {earlier > 0 ? <Text dimColor>{`Atlan · ${earlier} earlier ${earlier === 1 ? 'call' : 'calls'} (ctrl+o shows all)`}</Text> : ''}
        {shown.map(call => (
          <Box flexDirection="column">
            <Text>
              <Text bold>Atlan</Text>
              {` · ${describeCall(call.tool, call.input)}`}
              <Text dimColor>{call.isInterrupted ? ' (interrupted)' : call.isRunning ? ' …' : ''}</Text>
            </Text>
            {call.isRunning || call.isInterrupted ? (
              ''
            ) : (
              <Text dimColor={!call.isErrored}>{`  ⎿ ${summarizeResult(resultText({ result: call.output }), call.isErrored)}`}</Text>
            )}
          </Box>
        ))}
      </Box>
    )
  })

  // 2 and 3. The Atlan pane: questions waiting on the person, then what this session touched.
  on('ui.render', { component: 'Pane', requestId: PANE }, ($, e, next) => {
    if (e.component !== 'Pane') return next(e)
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const width = Math.max(30, e.props.bodyColumns - 2)
    const cards = [...mem.cards.values()]
    return (
      <Box flexDirection="column">
        {cards.map(card => (
          <Box key={`card-${card.id}`} flexDirection="column" borderStyle="round" paddingX={1} marginBottom={1}>
            <Text bold>Needs your answer: {card.title}</Text>
            <Text dimColor>{clip(card.where, width)}</Text>
            <Text>
              {card.count < 0
                ? 'Could not read what depends on it.'
                : card.count === 0
                  ? 'Nothing in the registry depends on it.'
                  : `${card.count} ${card.count === 1 ? 'artifact depends' : 'artifacts depend'} on it:`}
            </Text>
            {card.dependents.slice(0, 8).map(one => (
              <Text>{`  ${one.kind} ${one.id} (${one.rel.replace(/_/g, ' ')})`}</Text>
            ))}
            {card.count > 8 ? <Text dimColor>{`  and ${card.count - 8} more`}</Text> : ''}
            {card.consequence ? <Text dimColor>{card.consequence}</Text> : ''}
            <Text dimColor>Answer in the permission prompt.</Text>
          </Box>
        ))}
        <Text bold>This session in Atlan</Text>
        {mem.touches.length === 0 ? <Text dimColor>Nothing yet. Artifacts Claude reads or changes in Atlan show here.</Text> : ''}
        {mem.touches.slice(0, 30).map(one => (
          <Box key={`touch-${one.id}`} flexDirection="row" justifyContent="space-between">
            <Text wrap="truncate-end">
              <Text dimColor>{`${one.action} `}</Text>
              {`${one.kind} ${one.name || one.id}${one.version ? ` ${one.version}` : ''}`}
              <Text dimColor>{one.status && one.status !== 'active' ? ` · ${one.status.replace(/_/g, ' ')}` : ''}</Text>
            </Text>
            <Button key={`copy-${one.id}`} label="Copy id" plain onPress={press => void $.ui.copy({ text: one.id, surface: press.surface })} />
          </Box>
        ))}
        <Link href={DASHBOARD} label="Open the Atlan dashboard" />
      </Box>
    )
  })

  // 4. The band above the prompt while skills publish, until the next request.
  on('ui.render', { component: 'AbovePrompt' }, ($, e, next) => {
    if (e.component !== 'AbovePrompt' || e.props.hasSurvey || mem.isBandHidden || mem.publishes.length === 0) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const isDone = mem.publishes.every(one => SETTLED.has(one.state))
    const blocked = mem.publishes.some(one => one.state === 'blocked')
    return (
      <Box flexDirection="column">
        <Box flexDirection="row" justifyContent="space-between">
          <Text bold>{isDone ? 'Atlan publish finished' : 'Publishing to Atlan'}</Text>
          <Button key="hide-publish" label="Hide" plain onPress={() => {
            mem.isBandHidden = true
            redraw($)
          }} />
        </Box>
        {mem.publishes.map(one => (
          <Text dimColor={one.state === 'unchanged'}>{`${MARK[one.state]} ${one.text}`}</Text>
        ))}
        {blocked ? <Text dimColor>Ask Claude to show the scan's findings for a blocked skill.</Text> : ''}
      </Box>
    )
  })

  // 5. The skill picker: search the organization's skills, pick one to load.
  on('ui.render', { component: 'Pane', requestId: PICKER }, ($, e, next) => {
    if (e.component !== 'Pane') return next(e)
    const elements = $.ui.resolve(e)
    const { Box, Text, Button } = elements
    const picker = mem.picker
    const room = Math.max(3, (e.props.scroll?.bodyRows ?? 20) - 4)
    const status =
      picker.state === 'searching'
        ? `Searching for "${picker.query}"…`
        : picker.state === 'failed'
          ? `The search failed. ${picker.note}`
          : picker.state === 'done'
            ? picker.rows.length === 0
              ? `No skill matches "${picker.query}".`
              : `${picker.total} found for "${picker.query}". Pick one to put it in your prompt.`
            : 'Search your organization’s skills by what they do.'
    return (
      <Box flexDirection="column">
        {'Input' in elements ? (
          <elements.Input
            key="skill-query"
            placeholder="What should the skill do?"
            value={picker.query}
            submitLabel="Search"
            autoFocus
            onSubmit={value => void pickerSearch($, mem, value)}
          />
        ) : (
          <Text dimColor>Run /atlan skills followed by words to search.</Text>
        )}
        <Text dimColor>{status}</Text>
        {picker.rows.slice(0, room).map(row => (
          <Button key={`skill-${row.id}`} plain onPress={() => void pickSkill($, row)}>
            {row.name}
            <Text dimColor>{`${row.version ? ` ${row.version}` : ''}${row.description ? ` · ${clip(row.description, 70)}` : ''}`}</Text>
          </Button>
        ))}
      </Box>
    )
  })
}
