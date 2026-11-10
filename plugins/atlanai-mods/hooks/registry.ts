// Pure helpers for the Atlan gateway's search-mode tools. Nothing here calls `$`, so every rule
// is tested without a session.

/** The Atlan gateway's tools this mod handles, as Claude Code names them, under whatever key the
 * server was added with: `mcp__plugin_atlanai_atlan__mcp__call_tool` for the plugin's server,
 * `mcp__atlan__mcp__call_tool` for one added by hand, and `mcp__atlan-prod__mcp__call_tool` for a
 * gateway someone registered under another name. They are search mode's six helpers
 * (`mcp__call_tool` inside the server's namespace) and the six registry reads search mode lists
 * by name (`registry__get_artifact`). The tool names are the gateway's own, so they identify it
 * without the key. */
export const GATEWAY_TOOL =
  /^mcp__[A-Za-z0-9_-]+?__(?:mcp__(?:call_tool|call_read_tool|call_write_tool|call_read_tools|search_tools|describe_tools)|registry__(?:search_artifacts|get_artifact|list_workspaces|get_artifact_counts|get_related_artifacts|get_file_content))$/
/** The three runners: `mcp__call_tool` runs any tool, `mcp__call_read_tool` only a read-only one
 * and `mcp__call_write_tool` anything that is not destructive. The gateway refuses a tool above a
 * runner's tier, so the runner caps what a call through it can do. */
export const CALL_TOOL = /^mcp__[A-Za-z0-9_-]+?__mcp__call(?:_read|_write)?_tool$/
/** The batch runner: up to eight reads in one call, each run as `mcp__call_read_tool` runs it. */
export const BATCH_TOOL = /^mcp__[A-Za-z0-9_-]+?__mcp__call_read_tools$/
/** The registry reads search mode lists by name. Their input is the tool's own arguments, with
 * `session_id` and `rationale` among them. The gateway lists a name here only while the registry
 * declares it read-only; this mod still classifies each call by its annotations. */
export const DIRECT_READ =
  /^mcp__[A-Za-z0-9_-]+?__registry__(?:search_artifacts|get_artifact|list_workspaces|get_artifact_counts|get_related_artifacts|get_file_content)$/
/** Every tool whose permission check this mod answers: the runners, the batch runner and the
 * reads listed by name. */
export const CHECKED_TOOL =
  /^mcp__[A-Za-z0-9_-]+?__(?:mcp__call(?:_read|_write)?_tools?|registry__(?:search_artifacts|get_artifact|list_workspaces|get_artifact_counts|get_related_artifacts|get_file_content))$/
const RUNNER = /^mcp__call(?:_read|_write)?_tool$/
const TOOL_PARTS = /^mcp__([A-Za-z0-9_-]+?)__((?:mcp|registry)__\w+)$/

/** The gateway's own name for a tool Claude Code names: `registry__get_artifact` in
 * `mcp__atlan__registry__get_artifact`, `mcp__call_tool` in `mcp__atlan__mcp__call_tool`. */
export function gatewayNameOf(tool: string): string | undefined {
  return GATEWAY_TOOL.test(tool) ? TOOL_PARTS.exec(tool)?.[2] : undefined
}

/** The most a call through this runner can do, as the gateway enforces it; `undefined` for
 * `mcp__call_tool`, which runs any tier. */
export function runnerCap(tool: string): Effect | undefined {
  if (/__mcp__call_read_tools?$/.test(tool)) return 'read'
  if (/__mcp__call_write_tool$/.test(tool)) return 'write'
  return undefined
}

/** An effect no greater than the runner allows. The gateway refuses a tool above its runner's
 * tier, so a gateway tool called through `mcp__call_read_tool` only reads. Another server's tool
 * stays `unknown`: its tier comes from its own hints, which this mod does not trust. The
 * always-confirm tools keep their question: a refused call costs nothing, and a wrong cap would. */
export function capped(effect: Effect, cap: Effect | undefined, name: string): Effect {
  if (cap === undefined || effect === 'unknown' || ALWAYS_CONFIRM.test(canonical(name))) return effect
  if (cap === 'read') return 'read'
  return effect === 'destructive' ? 'write' : effect
}

/** The servers a probe tries, as /mcp lists them: the plugin's, then one added by hand. */
export const PROBE_SERVERS = ['plugin:atlanai:atlan', 'atlan'] as const

/** The server key in a gateway tool's name: `atlan-prod` in `mcp__atlan-prod__mcp__call_tool`. */
export function serverKeyOf(tool: string): string | undefined {
  return GATEWAY_TOOL.test(tool) ? TOOL_PARTS.exec(tool)?.[1] : undefined
}

/** The servers the mod's own checks try: a gateway the model has called under a key of its own
 * first, then the usual two. A plugin's key (`plugin_…`) is one of those two already. */
export function probeServers(seen: string | undefined): string[] {
  const own = seen && !seen.startsWith('plugin_') && !(PROBE_SERVERS as readonly string[]).includes(seen) ? [seen] : []
  return [...own, ...PROBE_SERVERS]
}

/** What a call through `mcp__call_tool` does to the registry. */
export type Effect = 'read' | 'write' | 'destructive' | 'unknown'

/** A tool's annotations as `mcp__describe_tools` and `mcp__search_tools` return them. */
export type Hints = { readOnly?: boolean; destructive?: boolean }
export type HintTable = Record<string, Hints>

/** The registry tools that take something away from other people, whatever their annotations
 * say: `move_artifact` is annotated non-destructive, but it removes the artifact from everyone in
 * its current workspace. */
const ALWAYS_CONFIRM = /^registry__(?:archive_artifact|approve_artifact|move_artifact)$/
const REGISTRY_READ = /^registry__(?:get|list|search|query)_\w+$/
/** The gateway's own discovery tools, which read the catalogue and change nothing. */
const GATEWAY_READ = /^mcp__(?:search_tools|describe_tools)$/
/** Tools the Atlan gateway itself serves: the registry's, and the apps' (`extension__skill__create`).
 * Only their annotations are trusted: another server's `readOnlyHint` is its own claim. */
const GATEWAY_OWNED = /^(?:registry|extension)__/

/** A tool name as the gateway resolves it: case and surrounding space do not make another tool. */
export function canonical(name: string): string {
  return name.trim().toLowerCase()
}

/**
 * Decides a call's effect from the registry's naming and annotations.
 *
 * Annotations count only for the gateway's own tools. Another server's tool is `unknown`, so the
 * session's own rules decide it, unless its server marks it destructive, which can only add a
 * question. A nested gateway tool (`mcp__call_tool` inside `mcp__call_tool`) is unwrapped by
 * [unwrap] before this; any other `mcp__` name left over asks.
 */
export function classify(name: string, hints: HintTable): Effect {
  const key = canonical(name)
  if (ALWAYS_CONFIRM.test(key)) return 'destructive'
  if (GATEWAY_READ.test(key)) return 'read'
  if (key.startsWith('mcp__')) return 'write'
  const hint = hints[key] ?? hints[name]
  if (!GATEWAY_OWNED.test(key)) return hint?.destructive ? 'destructive' : 'unknown'
  if (hint?.destructive) return 'destructive'
  if (hint?.readOnly) return 'read'
  if (hint) return 'write'
  if (REGISTRY_READ.test(key)) return 'read'
  return 'write'
}

/** The call a runner will really run: a nested runner (`mcp__call_tool` and its siblings) names another call inside
 * its own `arguments`, so a write cannot hide one level down. Stops after four levels. */
export function unwrap(name: unknown, args: unknown): { name: string; args: unknown } {
  let current = { name: typeof name === 'string' ? name : '', args }
  for (let depth = 0; depth < 4; depth += 1) {
    if (!RUNNER.test(canonical(current.name))) break
    // Arguments sent as a JSON string are read the same way, so a write cannot hide in one.
    const inner = argsOf(current.args)
    current = { name: typeof inner.name === 'string' ? inner.name : '', args: inner.arguments }
  }
  return current
}

/** The calls a batch names, each unwrapped as a runner's would be. `undefined` when `calls` is
 * not a list of calls, which the gateway refuses before running anything. */
export function batchCalls(input: unknown): { name: string; args: unknown }[] | undefined {
  const calls = argsOf(input).calls
  if (!Array.isArray(calls) || calls.length === 0 || !calls.every(isRecord)) return undefined
  return calls.map(call => unwrap(call.name, call.arguments))
}

/** The most a batch can do: its worst call, each capped at a read as the gateway caps it. One
 * call the mod cannot judge leaves the whole batch to the session's own rules. */
export function batchEffect(calls: readonly { name: string }[], hints: HintTable): Effect {
  const effects = calls.map(call => capped(classify(call.name, hints), 'read', call.name))
  if (effects.includes('destructive')) return 'destructive'
  if (effects.includes('unknown')) return 'unknown'
  return effects.includes('write') ? 'write' : 'read'
}

/** One answer in a batch's result. */
export type BatchEntry = { name: string; text: string; isError: boolean }

/** A batch's answers, in the order of its calls: `{"results":[{name, isError, content}]}`. */
export function batchEntries(text: string): BatchEntry[] | undefined {
  for (const object of jsonObjects(text)) {
    if (!isRecord(object) || !Array.isArray(object.results) || object.results.length === 0) continue
    const entries = object.results.filter(isRecord)
    if (entries.length !== object.results.length || !entries.every(entry => typeof entry.isError === 'boolean')) continue
    return entries.map(entry => ({
      name: typeof entry.name === 'string' ? entry.name : '',
      text: isText(entry.error) ? entry.error : resultText({ result: entry }),
      isError: entry.isError === true,
    }))
  }
  return undefined
}

/** The fields the gateway reads beside `name` and `arguments`, never inside them. */
const RAILS = ['session_id', 'rationale', 'objective'] as const
type Rail = (typeof RAILS)[number]

export type CallInput = {
  name?: unknown
  arguments?: unknown
  session_id?: unknown
  rationale?: unknown
  objective?: unknown
}

export type Repair = {
  input: CallInput
  /** What changed, for a note to the model; empty when nothing did. */
  changes: string[]
}

const isText = (value: unknown): value is string => typeof value === 'string' && value.trim() !== ''
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/**
 * Moves rails the model put inside `arguments` out to the top level, and fills `session_id` from
 * the session this Claude Code session already opened.
 *
 * A rail is moved only where it cannot be the tool's own argument: `session_id` when it holds a
 * gateway session (`mcp_session_…`), and `rationale` or `objective` only for the tools the gateway
 * serves itself (`registry__…` and the apps' `extension__…`), which take neither. Another server's
 * tool may have a field of the same name.
 */
export function repairRails(input: CallInput, known: string, isCallTool: boolean): Repair {
  const changes: string[] = []
  let next: CallInput = { ...input }
  const name = typeof input.name === 'string' ? input.name : ''
  if (isCallTool && isRecord(input.arguments)) {
    const args: Record<string, unknown> = { ...input.arguments }
    for (const rail of RAILS) {
      const value = args[rail]
      const movable =
        rail === 'session_id' ? isText(value) && value.startsWith('mcp_session_') : isText(value) && GATEWAY_OWNED.test(name)
      if (!movable) continue
      delete args[rail]
      if (!isText(next[rail as Rail])) next = { ...next, [rail]: value }
      changes.push(`moved ${rail} out of arguments`)
    }
    if (changes.length > 0) next = { ...next, arguments: args }
  }
  if (!isText(next.session_id) && known.startsWith('mcp_session_')) {
    next = { ...next, session_id: known }
    changes.push('reused the open session_id')
  }
  return { input: next, changes }
}

/** The gateway session a result names, from `mcp_session_id` or `session_id`. */
export function findSessionId(text: string): string | undefined {
  // A batch carries each answer as JSON inside a JSON string, so its quotes arrive escaped.
  const match = /\\?"(?:mcp_)?session_id\\?"\s*:\s*\\?"(mcp_session_[A-Za-z0-9]+)\\?"/.exec(text)
  return match?.[1]
}

/** The gateway's refusals of a `session_id` it cannot use, word for word: search mode's, full
 * mode's, and the older search-mode wording. */
const STALE_SESSION = [
  'session not found; call `mcp__search_tools`',
  'the requested session is unavailable',
  'names no session',
]

/** True only when the gateway refused the session it was given. A tool's own "not found" (an
 * artifact, a file) is not one, though every result carries a session receipt beside it. */
export function isStaleSession(text: string): boolean {
  const lower = text.toLowerCase()
  return STALE_SESSION.some(refusal => lower.includes(refusal.toLowerCase()))
}

/** A failure worth trying again: the gateway or a server behind it was briefly unavailable. */
export type Retry = { retryable: true; afterSeconds: number }

/**
 * Whether a failed call is a transient one, and how long to wait: the gateway's own
 * `retry_after_seconds` when it says, else 2 seconds. Bad gateway, unavailable and timeout
 * answers qualify, as does the gateway's `retryable: true`; a refusal, a 4xx or a sign-in does not.
 */
export function retryOf(text: string): Retry | undefined {
  if (/"retryable"\s*:\s*false/.test(text)) return undefined
  const transient =
    /"retryable"\s*:\s*true/.test(text) ||
    /\b(?:502|503|504)\b|bad gateway|service unavailable|gateway time-?out|temporarily unavailable|session could not be opened; retry/i.test(text)
  if (!transient) return undefined
  const after = /"retry_after_seconds"\s*:\s*(\d+)/.exec(text)
  const seconds = after ? Number(after[1]) : 2
  return { retryable: true, afterSeconds: Math.min(Math.max(seconds, 1), 60) }
}

/** What the model reads after a transient failure: retry a read, never repeat a write blindly. */
export function retryNote(effect: Effect, retry: Retry): string {
  if (effect === 'read')
    return `Atlan was briefly unavailable. This call only reads, so it is safe to repeat: wait ${retry.afterSeconds} seconds, then make the same call again, doubling the wait each time, at most 3 tries. If it still fails, tell the person Atlan is not answering and that results may be incomplete.`
  return 'Atlan was briefly unavailable, and this call may already have run. Do not repeat it as it is: read the artifact back first (or repeat a create with the same idempotency_key), then retry only what did not happen.'
}

/** Every top-level JSON object in a text, in order. The gateway appends a second object (the
 * session receipt) after a tool's own result, so one `JSON.parse` of the whole text fails. */
export function jsonObjects(text: string): unknown[] {
  const found: unknown[] = []
  let depth = 0
  let start = -1
  let inString = false
  let escaped = false
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i]
    if (inString) {
      if (escaped) escaped = false
      else if (char === '\\') escaped = true
      else if (char === '"') inString = false
      continue
    }
    if (char === '"') inString = true
    else if (char === '{') {
      if (depth === 0) start = i
      depth += 1
    } else if (char === '}' && depth > 0) {
      depth -= 1
      if (depth === 0 && start >= 0) {
        try {
          found.push(JSON.parse(text.slice(start, i + 1)))
        } catch {
          // not JSON after all: skip it
        }
        start = -1
      }
    }
  }
  return found
}

/** The annotations in a `describe_tools` or `search_tools` result, by tool name. */
export function harvestHints(text: string): HintTable {
  const table: HintTable = {}
  for (const object of jsonObjects(text)) {
    if (!isRecord(object) || !Array.isArray(object.tools)) continue
    for (const tool of object.tools) {
      if (!isRecord(tool) || typeof tool.name !== 'string' || !isRecord(tool.annotations)) continue
      const { readOnlyHint, destructiveHint } = tool.annotations
      table[tool.name] = { readOnly: readOnlyHint === true, destructive: destructiveHint === true }
    }
  }
  return table
}

/** A call's `arguments` as an object. A model sometimes sends them as a JSON string when the
 * tool's schema leaves their type open; the gateway's own schema asks for an object. */
export function argsOf(value: unknown): Record<string, unknown> {
  if (isRecord(value)) return value
  if (typeof value === 'string' && value.trim().startsWith('{')) {
    try {
      const parsed: unknown = JSON.parse(value)
      if (isRecord(parsed)) return parsed
    } catch {
      // not JSON: no arguments to read
    }
  }
  return {}
}

/** The artifact a write names, for the confirmation's wording. */
export function artifactOf(value: unknown): string | undefined {
  const args = argsOf(value)
  for (const key of ['artifact_id', 'id', 'subject_id']) {
    if (isText(args[key])) return args[key] as string
  }
  return undefined
}

/** The kind and name a create carries, for the confirmation's wording. */
function creation(args: Record<string, unknown>): string {
  const meta = isRecord(args.metadata) ? args.metadata : {}
  const kind = isText(args.kind) ? args.kind : 'artifact'
  const name = isText(meta.display_name) ? meta.display_name : isText(meta.name) ? meta.name : ''
  const workspace = isText(meta.workspace_id) ? ` in ${meta.workspace_id}` : ''
  return `${kind}${name ? ` "${name}"` : ''}${workspace}`
}

/** One sentence naming what a write will do, shown in the permission prompt. */
export function describeWrite(name: string, args: unknown): string {
  if (!GATEWAY_OWNED.test(canonical(name))) return `Atlan gateway call: ${name.slice(0, 80)}, which its server marks as changing something.`
  const record = argsOf(args)
  const verb = name.replace(/^registry__/, '').replace(/_/g, ' ')
  // A withdrawal names a release (`rel_…`), not an artifact.
  const release = isText(record.release_id) ? record.release_id : ''
  const target = name.includes('create') ? creation(record) : (artifactOf(record) ?? release)
  const consequence = CONSEQUENCE[canonical(name)]
  return `Atlan registry write: ${verb}${target ? ` ${target}` : ''}.${consequence ? ` ${consequence}` : ''}`
}

export type Related = { count: number; dependents: { id: string; kind: string; rel: string }[] }

/** The artifacts that point at this one, from `get_related_artifacts`. */
export function dependentsOf(text: string): Related | undefined {
  for (const object of jsonObjects(text)) {
    if (!isRecord(object) || !Array.isArray(object.incoming)) continue
    const dependents = object.incoming.flatMap(edge => {
      if (!isRecord(edge) || !isRecord(edge.from) || !isText(edge.from.id)) return []
      if (edge.relationship_status !== undefined && edge.relationship_status !== 'active') return []
      return [
        {
          id: edge.from.id as string,
          kind: isText(edge.from.kind) ? (edge.from.kind as string) : 'artifact',
          rel: isText(edge.rel_type) ? (edge.rel_type as string) : 'related_to',
        },
      ]
    })
    return { count: dependents.length, dependents }
  }
  return undefined
}

export type Summary = { name: string; kind: string; version: string; workspace: string; status: string }

/** The few envelope fields of `get_artifact` a confirmation names. */
export function summaryOf(text: string): Summary | undefined {
  for (const object of jsonObjects(text)) {
    if (!isRecord(object) || !isText(object.id)) continue
    const field = (key: string) => (isText(object[key]) ? (object[key] as string) : '')
    return {
      name: field('display_name') || field('name'),
      kind: field('kind'),
      version: field('version_label'),
      workspace: field('workspace_id'),
      status: field('artifact_status'),
    }
  }
  return undefined
}

/** What an archive, move, approve, release or withdrawal does to everyone else. */
export const CONSEQUENCE: Record<string, string> = {
  registry__archive_artifact: 'Archiving hides it from search, listings and installs for everyone.',
  registry__move_artifact: 'Moving takes it away from everyone in its current workspace.',
  registry__approve_artifact: 'Approving overrides the security scan for this version.',
  registry__release_artifact: 'Releasing makes this version the one its channel serves to everyone who installs or loads it.',
  registry__withdraw_release:
    'Withdrawing moves everyone on its channel back to the previous release, or to none, and cannot be undone: only a new release replaces it.',
}

/** The permission prompt's text for an archive, move or approve. */
export function describeBlastRadius(name: string, id: string, summary?: Summary, related?: Related): string {
  const verb = name.replace(/^registry__/, '').replace(/_artifact$/, '')
  const label = summary?.name ? `${summary.kind || 'artifact'} "${summary.name}"` : id
  const version = summary?.version ? ` ${summary.version}` : ''
  const where = summary?.workspace ? ` in ${summary.workspace}` : ''
  const parts = [`Atlan: ${verb} ${label}${version}${where}.`]
  if (related === undefined) parts.push('Could not read what depends on it.')
  else if (related.count === 0) parts.push('Nothing in the registry depends on it.')
  else {
    const shown = related.dependents.slice(0, 3).map(d => `${d.kind} ${d.id}`)
    const more = related.count > 3 ? ` and ${related.count - 3} more` : ''
    parts.push(`${related.count} ${related.count === 1 ? 'artifact depends' : 'artifacts depend'} on it: ${shown.join(', ')}${more}.`)
  }
  const consequence = CONSEQUENCE[canonical(name)]
  if (consequence) parts.push(consequence)
  return parts.join(' ')
}

/** What a failed connection check means, from its error text. Never echoes the text itself. */
export function diagnose(text: string): 'sign-in' | 'missing' | 'unreachable' | 'degraded' | 'error' {
  if (/\b(401|unauthori[sz]ed|sign.?in|log.?in|authenticat|oauth|token)\b/i.test(text)) return 'sign-in'
  if (retryOf(text)) return 'degraded'
  if (/(not found|no such|unknown server|not connected|is not configured|no server)/i.test(text)) return 'missing'
  if (/(proxy|tunnel|ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|network|timed? ?out|403|fetch failed|getaddrinfo)/i.test(text))
    return 'unreachable'
  return 'error'
}

/** The Atlan connection as the last check or call found it. */
export type AtlanHealth = {
  state: 'unknown' | 'ready' | 'sign-in' | 'missing' | 'unreachable' | 'degraded' | 'error'
  /** The server name the check reached, as /mcp lists it. */
  server: string
  /** A short reason, never a token or a response body. */
  detail: string
  /** Milliseconds since the epoch. */
  checkedAt: number
}

/** The fix for each state, in one line. */
export const FIX: Record<string, string> = {
  ready: 'Connected. Registry reads run without a prompt; writes ask first.',
  'sign-in': 'Run /mcp, choose plugin:atlanai:atlan, then Authenticate.',
  missing: 'Install the atlanai plugin: /plugin install atlanai@atlan, then run /mcp to sign in.',
  unreachable: 'Claude Code cannot reach api.atlan.com. Check your network or proxy, then run /atlan again.',
  degraded: 'Atlan is answering some calls with temporary errors. Reads are retried; run /atlan to check again.',
  error: 'The Atlan server answered with an error. Run /atlan again; if it persists, ask Claude to troubleshoot Atlan.',
  unknown: 'Not checked yet. Run /atlan to check.',
}

/** Rows for `/atlan search`, from a `search_artifacts` result. */
export function searchRows(text: string): { total: number; rows: string[] } | undefined {
  for (const object of jsonObjects(text)) {
    if (!isRecord(object) || !Array.isArray(object.items)) continue
    const rows = object.items.flatMap(item => {
      if (!isRecord(item) || !isText(item.id)) return []
      const name = isText(item.display_name) ? item.display_name : isText(item.name) ? item.name : item.id
      const status = isText(item.artifact_status) && item.artifact_status !== 'active' ? ` [${item.artifact_status}]` : ''
      const version = isText(item.version_label) ? ` ${item.version_label}` : ''
      return [`${name}${version}${status}  ${item.id}`]
    })
    const page = isRecord(object.page) ? object.page : {}
    const total = typeof page.total === 'number' ? page.total : rows.length
    return { total, rows }
  }
  return undefined
}

/** The text a tool result carries, whichever field holds it. */
export function resultText(ran: { text?: unknown; result?: unknown }): string {
  if (typeof ran.text === 'string') return ran.text
  const result = ran.result
  if (typeof result === 'string') return result
  if (isRecord(result) && Array.isArray(result.content)) {
    return result.content.map(block => (isRecord(block) && typeof block.text === 'string' ? block.text : '')).join('\n')
  }
  if (Array.isArray(result)) {
    return result.map(block => (isRecord(block) && typeof block.text === 'string' ? block.text : '')).join('\n')
  }
  try {
    return JSON.stringify(result) ?? ''
  } catch {
    return ''
  }
}
