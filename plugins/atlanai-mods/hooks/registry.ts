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

/** Decides a call's effect from its annotations, then from the registry's naming. A tool of
 * another server that the table has not seen is `unknown`: the session's own rules decide it. */
export function classify(name: string, hints: HintTable): Effect {
  if (ALWAYS_CONFIRM.test(name)) return 'destructive'
  const hint = hints[name]
  if (hint?.destructive) return 'destructive'
  if (hint?.readOnly) return 'read'
  if (hint) return 'write'
  if (REGISTRY_READ.test(name)) return 'read'
  if (name.startsWith('registry__')) return 'write'
  return 'unknown'
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

/** True when a refusal says the session it was given is unknown or closed. */
export function isStaleSession(text: string): boolean {
  return /session/i.test(text) && /(names no session|not found|unknown|expired|closed|invalid)/i.test(text)
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

/** The artifact a write names, for the confirmation's wording. */
export function artifactOf(args: unknown): string | undefined {
  if (!isRecord(args)) return undefined
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
  const record = isRecord(args) ? args : {}
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

const CONSEQUENCE: Record<string, string> = {
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
export function diagnose(text: string): 'sign-in' | 'missing' | 'unreachable' | 'error' {
  if (/\b(401|unauthori[sz]ed|sign.?in|log.?in|authenticat|oauth|token)\b/i.test(text)) return 'sign-in'
  if (/(not found|no such|unknown server|not connected|is not configured|no server)/i.test(text)) return 'missing'
  if (/(proxy|tunnel|ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|network|timed? ?out|403|fetch failed|getaddrinfo)/i.test(text))
    return 'unreachable'
  return 'error'
}

/** The fix for each state, in one line. */
export const FIX: Record<string, string> = {
  ready: 'Connected. Registry reads run without a prompt; writes ask first.',
  'sign-in': 'Run /mcp, choose plugin:atlanai:atlan, then Authenticate.',
  missing: 'Install the atlanai plugin: /plugin install atlanai@atlan, then run /mcp to sign in.',
  unreachable: 'Claude Code cannot reach api.atlan.com. Check your network or proxy, then run /atlan again.',
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
