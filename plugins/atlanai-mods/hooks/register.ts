// Registry Mods: a Claude Code mod for the atlanai plugin's `atlan` server.
//
// tool.check (mcp__call_tool): registry reads run without a prompt; writes ask, naming the
//   write; archive, approve and move always ask, naming what depends on the artifact.
// tool.call (the three gateway tools): moves session_id, rationale and objective out of
//   `arguments`, reuses the session's open audit session, refuses a call with no rationale, and
//   learns tool annotations and the session id from results.
// session.start: checks the connection without opening an audit session, and puts a problem,
//   never a success, on the status line. /atlan reports it; /atlan search searches the registry.
//
// The host reads `on(...)` and `$.noun.method(...)` from source, so both are spelled literally.

import type { EngineInterface, Register } from 'claude-code'

import type { AtlanHealth } from '../types'
import {
  CALL_TOOL,
  FIX,
  GATEWAY_TOOL,
  PROBE_SERVERS,
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
  searchRows,
  summaryOf,
  type CallInput,
  type HintTable,
} from './registry'

const SESSION = { plugin: 'atlanai-mods', key: 'session' } as const
const HEALTH = { plugin: 'atlanai-mods', key: 'health' } as const
const HINTS_KEY = 'hints'
const LOOKUP_MS = 6000
const PROBE_MS = 15000
const RETRY_MS = 20000

const RATIONALE_HELP =
  'Atlan needs a one-sentence `rationale` for every mcp__call_tool call, beside `name` and `arguments` (not inside `arguments`). Add it and call again.'
const GUARD_FAILED = 'The Atlan registry guard could not check this call, so it needs your confirmation.'
const USAGE = 'Usage: /atlan (connection status) or /atlan search <words>'

type Dollar = EngineInterface
type Lookup = { text: string; isError: boolean }

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

async function setHealth($: Dollar, health: AtlanHealth): Promise<void> {
  await $.state.set(HEALTH, health)
  const line: Record<AtlanHealth['state'], string | undefined> = {
    ready: undefined,
    unknown: undefined,
    'sign-in': 'Atlan: sign in with /mcp',
    missing: 'Atlan: not connected, run /atlan',
    unreachable: 'Atlan: offline, run /atlan',
    error: 'Atlan: error, run /atlan',
  }
  $.ui.status(line[health.state])
}

/** A gateway call made by the mod itself, under the session's audit session when one is open. */
async function lookup($: Dollar, server: string, name: string, args: Record<string, unknown>, rationale: string): Promise<Lookup> {
  const { value: session = '' } = await $.state.get(SESSION)
  const rails = session
    ? { session_id: session }
    : { objective: 'Claude Code with the Atlan registry: the Registry Mods plugin checks registry changes and connection status for the user.' }
  const result = await $.mcp.call(server, 'mcp__call_tool', { name, arguments: args, rationale, ...rails })
  const text = result.content.map(block => (isRecord(block) && typeof block.text === 'string' ? block.text : '')).join('\n')
  const opened = findSessionId(text)
  if (opened && opened !== session) await $.state.set(SESSION, opened)
  return { text, isError: result.isError }
}

/** Settles with `undefined` once `ms` pass, so a slow registry never holds a prompt. */
async function within<T>($: Dollar, ms: number, work: Promise<T>): Promise<T | undefined> {
  const stop = new AbortController()
  const timer = $.clock.sleep(ms, { signal: stop.signal }).then(
    () => undefined,
    () => undefined,
  )
  try {
    return await Promise.race([work.catch(() => undefined), timer])
  } finally {
    stop.abort()
  }
}

/** The permission prompt's text for an archive, move or approve, with what depends on it. */
async function blastRadius($: Dollar, tool: string, name: string, input: Record<string, unknown>): Promise<string> {
  const id = artifactOf(input.arguments)
  if (!id) return describeWrite(name, input.arguments)
  const server = tool.slice('mcp__'.length, tool.lastIndexOf('__mcp__'))
  const why = 'Checking what depends on this artifact before the user confirms the change.'
  const [related, artifact] = await Promise.all([
    within($, LOOKUP_MS, lookup($, server, 'registry__get_related_artifacts', { artifact_id: id }, why)),
    within($, LOOKUP_MS, lookup($, server, 'registry__get_artifact', { artifact_id: id }, why)),
  ])
  return describeBlastRadius(
    name,
    id,
    artifact && !artifact.isError ? summaryOf(artifact.text) : undefined,
    related && !related.isError ? dependentsOf(related.text) : undefined,
  )
}

/** Checks the connection with `mcp__describe_tools`, which opens no audit session. */
async function probe($: Dollar): Promise<AtlanHealth> {
  let detail = 'no Atlan server answered'
  let state: AtlanHealth['state'] = 'missing'
  for (const server of PROBE_SERVERS) {
    try {
      const result = await within($, PROBE_MS, $.mcp.call(server, 'mcp__describe_tools', { servers: ['registry'], detail: 'summary' }))
      if (result === undefined) {
        state = 'unreachable'
        detail = `${server} did not answer within ${PROBE_MS / 1000} seconds`
        continue
      }
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

async function report($: Dollar, health: AtlanHealth, confirmWrites: boolean): Promise<string> {
  const { value: session = '' } = await $.state.get(SESSION)
  const lines = [
    health.state === 'ready' ? `Atlan: connected through ${health.server} (${health.detail}).` : `Atlan: ${health.state} (${health.detail}).`,
    FIX[health.state] ?? FIX.error,
  ]
  if (health.state === 'ready') {
    lines.push(
      confirmWrites
        ? 'Writes ask first. Archive, approve and move always ask, with what depends on the artifact.'
        : 'Writes follow your permission rules. Archive, approve and move always ask.',
    )
    lines.push(session ? 'An audit session is open and is reused for every call.' : 'No audit session yet: the first registry call opens one.')
  }
  return lines.join('\n')
}

async function search($: Dollar, words: string): Promise<string> {
  if (!words) return USAGE
  const { value: health } = await $.state.get(HEALTH)
  const servers = health?.state === 'ready' && health.server ? [health.server] : [...PROBE_SERVERS]
  for (const server of servers) {
    try {
      const found = await within(
        $,
        PROBE_MS,
        lookup(
          $,
          server,
          'registry__search_artifacts',
          { query: words, semantic: true, limit: 10 },
          'The user searched the registry with the /atlan search command.',
        ),
      )
      if (!found) continue
      if (found.isError) return `Atlan refused the search. ${FIX[diagnose(found.text)] ?? FIX.error}`
      const rows = searchRows(found.text)
      if (!rows || rows.rows.length === 0) return `Nothing in Atlan matches "${words}".`
      const more = rows.total > rows.rows.length ? `, showing ${rows.rows.length}` : ''
      return [`${rows.total} found for "${words}"${more}:`, ...rows.rows.map(row => `  ${row}`)].join('\n')
    } catch {
      // try the next server name
    }
  }
  return `Atlan did not answer. ${FIX.unreachable}`
}

export const register: Register = (on, options) => {
  const confirmWrites = options.confirm_writes !== false

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'atlan',
      description: 'Atlan connection status, or /atlan search <words> to search the registry',
      argumentHint: '[search <words>]',
      immediate: true,
    })
    const check = async (): Promise<void> => {
      const health = await probe($)
      // MCP servers can still be connecting at start: report a problem only if it outlasts a retry.
      if (health.state === 'ready') await setHealth($, health)
      else
        $.clock.after(RETRY_MS, async () => {
          await setHealth($, await probe($))
        })
    }
    void check().catch(() => undefined)
    return next(e)
  })

  on('command.run', { command: 'atlan' }, async ($, e) => {
    const [sub = '', ...rest] = e.args.trim().split(/\s+/)
    if (sub === 'search') return { text: await search($, rest.join(' ').trim()) }
    if (sub !== '' && sub !== 'status' && sub !== 'doctor') return { text: USAGE }
    const health = await probe($)
    await setHealth($, health)
    return { text: await report($, health, confirmWrites) }
  })

  on('tool.call', { tool: GATEWAY_TOOL }, async ($, e, next) => {
    const call = e as unknown as CallInput & { tool: string }
    const isCallTool = CALL_TOOL.test(call.tool)
    const { value: known = '' } = await $.state.get(SESSION)
    const { input } = repairRails(call, known, isCallTool)
    if (isCallTool && (typeof input.rationale !== 'string' || input.rationale.trim() === '')) {
      return { deny: RATIONALE_HELP }
    }

    const ran = await next({ ...e, ...input } as typeof e)
    if (ran.deny !== undefined) return ran

    const text = resultText(ran)
    const opened = findSessionId(text)
    if (opened && opened !== known) await $.state.set(SESSION, opened)
    else if (ran.isError && known && input.session_id === known && isStaleSession(text)) await $.state.set(SESSION, '')

    if (!isCallTool) await saveHints($, harvestHints(text))

    const { value: health } = await $.state.get(HEALTH)
    if (!ran.isError && health?.state !== 'ready') {
      await setHealth($, { state: 'ready', server: health?.server ?? '', detail: 'a registry call succeeded', checkedAt: await $.clock.now() })
    } else if (ran.isError && diagnose(text) === 'sign-in') {
      await setHealth($, { state: 'sign-in', server: health?.server ?? '', detail: 'a registry call was refused', checkedAt: await $.clock.now() })
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('tool.check', { tool: CALL_TOOL }, async ($, e, next) => {
    const verdict = await next(e)
    if (verdict.decision === 'deny') return verdict

    const input = isRecord(e.input) ? e.input : {}
    const name = typeof input.name === 'string' ? input.name : ''
    const effect = classify(name, await loadHints($))

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
    return { decision: 'ask', reason: await blastRadius($, e.tool, name, input) }
  }).catch(() => ({ decision: 'ask', reason: GUARD_FAILED }))
}
