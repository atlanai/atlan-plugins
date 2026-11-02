// Pure helpers for the Atlan gateway's search-mode tools. Nothing here calls `$`, so every rule
// is tested without a session.

/** The gateway's three tools as Claude Code names them: `mcp__plugin_atlanai_atlan__mcp__call_tool`
 * for the plugin's server, `mcp__atlan__mcp__call_tool` for one added by hand. A variant's plugin
 * (`atlanai-dev`) keeps the server key `atlan`, so its name matches too. */
export const GATEWAY_TOOL = /^mcp__(?:plugin_[A-Za-z0-9_-]+_)?atlan__mcp__(?:call_tool|search_tools|describe_tools)$/
export const CALL_TOOL = /^mcp__(?:plugin_[A-Za-z0-9_-]+_)?atlan__mcp__call_tool$/

/** The servers a probe tries, as /mcp lists them: the plugin's, then one added by hand. */
export const PROBE_SERVERS = ['plugin:atlanai:atlan', 'atlan'] as const

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

/** The call `mcp__call_tool` will really run: a nested `mcp__call_tool` names another call inside
 * its own `arguments`, so a write cannot hide one level down. Stops after four levels. */
export function unwrap(name: unknown, args: unknown): { name: string; args: unknown } {
  let current = { name: typeof name === 'string' ? name : '', args }
  for (let depth = 0; depth < 4; depth += 1) {
    if (canonical(current.name) !== 'mcp__call_tool') break
    // Arguments sent as a JSON string are read the same way, so a write cannot hide in one.
    const inner = argsOf(current.args)
    current = { name: typeof inner.name === 'string' ? inner.name : '', args: inner.arguments }
  }
  return current
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
 * gateway session (`mcp_session_…`), and `rationale` or `objective` only for the registry's own
 * tools, which take neither. Another server's tool may have a field of the same name.
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
        rail === 'session_id' ? isText(value) && value.startsWith('mcp_session_') : isText(value) && name.startsWith('registry__')
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
  const match = /"(?:mcp_)?session_id"\s*:\s*"(mcp_session_[A-Za-z0-9]+)"/.exec(text)
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
  const target = name.includes('create') ? creation(record) : (artifactOf(record) ?? '')
  return `Atlan registry write: ${verb}${target ? ` ${target}` : ''}.`
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

/** What an archive, move or approve does to everyone else. */
export const CONSEQUENCE: Record<string, string> = {
  registry__archive_artifact: 'Archiving hides it from search, listings and installs for everyone.',
  registry__move_artifact: 'Moving takes it away from everyone in its current workspace.',
  registry__approve_artifact: 'Approving overrides the security scan for this version.',
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
  const consequence = CONSEQUENCE[name]
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
