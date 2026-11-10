// Pure helpers for what the mod draws: the transcript's Atlan rows, the side pane's list of
// artifacts this session touched, the confirmation card, the publish band and the skill picker.
// Nothing here calls `$`, so each rule is tested without a session.

import { CONSEQUENCE, argsOf, artifactOf, batchCalls, batchEntries, gatewayNameOf, jsonObjects, type Related, type Summary } from './registry'

const isText = (value: unknown): value is string => typeof value === 'string' && value.trim() !== ''
const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** Control, escape and direction-changing characters: registry text is written by other people, and
 * one of these in a name could move the terminal's cursor or reorder what is drawn around it. */
const UNSAFE = /[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2066-\u2069\ufeff]/g

/** Registry text made safe to draw: unsafe characters become spaces. */
export function plain(text: string): string {
  return text.replace(UNSAFE, ' ')
}

const field = (object: Record<string, unknown>, key: string): string => (isText(object[key]) ? plain(object[key] as string).trim() : '')

/** Cuts a line to `max` characters, ending it with an ellipsis when cut. */
export function clip(text: string, max: number): string {
  const line = plain(text).replace(/\s+/g, ' ').trim()
  return line.length > max ? `${line.slice(0, max - 1)}…` : line
}

/** `skill` → `skills`, for a search's wording. */
const plural = (kind: string) => (kind.endsWith('s') ? kind : `${kind}s`)

const PAST: Record<string, string> = {
  create: 'created',
  patch: 'updated',
  append: 'appended to',
  relate: 'linked',
  unrelate: 'unlinked',
  archive: 'archived',
  restore: 'restored',
  approve: 'approved',
  move: 'moved',
  release: 'released',
  delete: 'deleted',
  request: 'requested',
}

/** The skills a publish call names, from `extension__skill__create`'s `skills`. */
export function publishNames(value: unknown): string[] {
  const args = argsOf(value)
  if (!Array.isArray(args.skills)) return []
  return args.skills.map((skill, i) => (isRecord(skill) && isText(skill.name) ? clip(skill.name, 64) : `skill ${i + 1}`))
}

/** What one gateway call does, in a few words, for its transcript row. */
export function describeCall(tool: string, input: unknown): string {
  return clip(describeRaw(tool, input), 160)
}

function describeRaw(tool: string, input: unknown): string {
  const call = isRecord(input) ? input : {}
  if (tool.endsWith('__mcp__search_tools')) return isText(call.query) ? `searched tools for "${clip(call.query, 60)}"` : 'searched tools'
  if (tool.endsWith('__mcp__describe_tools')) {
    const servers = Array.isArray(call.servers) ? call.servers.filter(isText) : []
    return servers.length > 0 ? `read the tools of ${servers.join(', ')}` : 'read tool definitions'
  }
  if (tool.endsWith('__mcp__call_read_tools')) {
    const calls = batchCalls(call) ?? []
    if (calls.length === 0) return 'ran a batch of reads'
    if (calls.length === 1) return describeNamed(calls[0]?.name ?? '', argsOf(calls[0]?.args))
    const shown = calls.slice(0, 3).map(one => describeNamed(one.name, argsOf(one.args)))
    return `ran ${calls.length} reads: ${shown.join('; ')}${calls.length > 3 ? `; and ${calls.length - 3} more` : ''}`
  }
  // A read listed by name carries the tool's own arguments as its input.
  const direct = gatewayNameOf(tool)
  if (direct?.startsWith('registry__')) return describeNamed(direct, call)
  return describeNamed(isText(call.name) ? call.name : '', argsOf(call.arguments))
}

/** What one named gateway tool does with these arguments. */
function describeNamed(name: string, args: Record<string, unknown>): string {
  if (name === 'extension__skill__create') {
    const names = publishNames(args)
    return names.length > 0 ? `published ${names.length === 1 ? 'skill' : 'skills'} ${names.join(', ')}` : 'published a skill'
  }
  if (name === 'registry__search_artifacts') {
    const what = isText(args.kind) ? plural(args.kind) : 'the registry'
    return isText(args.query) ? `searched ${what} for "${clip(args.query, 60)}"` : `searched ${what}`
  }
  if (name === 'registry__get_artifact') return `read ${artifactOf(args) ?? 'an artifact'}`
  if (name === 'registry__get_related_artifacts') return `read what relates to ${artifactOf(args) ?? 'an artifact'}`
  if (name === 'registry__request_file_upload') return 'started a file upload'
  const registry = /^registry__([a-z]+)_(\w+)$/.exec(name)
  if (registry) {
    const [, verb = '', rest = ''] = registry
    const READ: Record<string, string> = { get: 'read', list: 'listed', query: 'queried', search: 'searched' }
    if (READ[verb]) return `${READ[verb]} ${rest.replace(/_/g, ' ')}`
    const target = verb === 'create' ? creationOf(args) : (artifactOf(args) ?? rest.replace(/_/g, ' '))
    return `${PAST[verb] ?? verb} ${target}`
  }
  const server = /^([a-z0-9-]+)__(.+)$/i.exec(name)
  if (server) return `${server[1]} · ${server[2]?.replace(/_/g, ' ')}`
  return name ? name.replace(/_/g, ' ') : 'called a tool'
}

/** The kind and name a create carries. */
function creationOf(args: Record<string, unknown>): string {
  const meta = isRecord(args.metadata) ? args.metadata : {}
  const kind = field(args, 'kind') || 'artifact'
  const name = field(meta, 'display_name') || field(meta, 'name') || field(args, 'name')
  return name ? `${kind} "${name}"` : kind
}

/** The tool's own result: the first object that is not only the gateway's session receipt. */
function resultObject(text: string): Record<string, unknown> | undefined {
  for (const object of jsonObjects(text)) {
    if (!isRecord(object)) continue
    const keys = Object.keys(object).filter(key => key !== 'com.atlan/receipt')
    if (keys.length === 0 || (keys.every(key => key === 'mcp_session_id' || key === 'next') && isText(object.mcp_session_id))) continue
    return object
  }
  return undefined
}

/** An artifact's name, version and status, as one short label. */
function label(object: Record<string, unknown>): string {
  const name = field(object, 'display_name') || field(object, 'name') || field(object, 'id')
  const version = field(object, 'version_label')
  const status = field(object, 'artifact_status')
  return [name, version, status && status !== 'active' ? `(${status.replace(/_/g, ' ')})` : ''].filter(Boolean).join(' ')
}

/** The rows a publish answers with, one per skill, whichever field holds them. */
function slotsOf(object: Record<string, unknown>): Record<string, unknown>[] {
  for (const key of ['skills', 'slots', 'results']) {
    const list = object[key]
    if (Array.isArray(list)) return list.filter(isRecord).map(flatten)
  }
  return []
}

/** A slot's own fields with those of the artifact it wraps, if it wraps one. */
function flatten(slot: Record<string, unknown>): Record<string, unknown> {
  for (const key of ['artifact', 'skill', 'result', 'data']) {
    const inner = slot[key]
    if (isRecord(inner)) return { ...inner, ...slot }
  }
  return slot
}

/** What a gateway call came back with, in a few words, for the line under its row. */
export function summarizeResult(text: string, isError: boolean): string {
  const batch = batchEntries(text)
  if (batch) {
    const failed = batch.filter(entry => entry.isError).length
    if (batch.length === 1 && batch[0]) return summarizeResult(batch[0].text, batch[0].isError)
    const parts = batch.slice(0, 3).map(entry => clip(summarizeResult(entry.text, entry.isError), 60))
    const head = failed === 0 ? `${batch.length} reads answered` : `${batch.length - failed} of ${batch.length} reads answered`
    return clip(`${head}: ${parts.join('; ')}${batch.length > 3 ? `; and ${batch.length - 3} more` : ''}`, 220)
  }
  if (isError) {
    const object = resultObject(text)
    const message = object ? field(object, 'message') || field(object, 'error') || field(object, 'detail') : ''
    return `Failed: ${clip(message || text.split('\n').find(line => line.trim() !== '') || 'no reason given', 140)}`
  }
  const object = resultObject(text)
  if (!object) return text.trim() === '' ? 'Done.' : clip(text, 140)
  if (Array.isArray(object.items)) {
    const page = isRecord(object.page) ? object.page : {}
    const total = typeof page.total === 'number' ? page.total : object.items.length
    const names = object.items.filter(isRecord).slice(0, 3).map(label)
    if (total === 0) return 'Nothing found.'
    const more = total > names.length ? `, and ${total - names.length} more` : ''
    return `${total} found: ${names.join(', ')}${more}`
  }
  const slots = slotsOf(object)
  if (slots.length > 0) return slots.map(slot => publishStep(slot, field(slot, 'name') || 'skill').text).join('; ')
  if (Array.isArray(object.tools)) return `${object.tools.length} ${object.tools.length === 1 ? 'tool' : 'tools'}`
  if (Array.isArray(object.incoming) || Array.isArray(object.outgoing)) {
    const incoming = Array.isArray(object.incoming) ? object.incoming.length : 0
    const outgoing = Array.isArray(object.outgoing) ? object.outgoing.length : 0
    return `${incoming} ${incoming === 1 ? 'artifact points' : 'artifacts point'} at it; it points at ${outgoing}`
  }
  if (isText(object.id)) return `${field(object, 'kind') || 'artifact'} ${label(object)}`
  return 'Done.'
}

/** An artifact this session read or changed, for the side pane. */
export type Touch = {
  id: string
  kind: string
  name: string
  version: string
  status: string
  /** What this session last did to it: `read`, or the past tense of a write. */
  action: string
}

/** The artifacts one call read or changed. Search hits are not touched: they were only listed. */
export function touchesOf(name: string, args: unknown, text: string, isError: boolean): Touch[] {
  if (isError || !name.startsWith('registry__') && name !== 'extension__skill__create') return []
  const object = resultObject(text)
  if (!object || name === 'registry__search_artifacts' || /^registry__(list|query)_/.test(name) || name === 'registry__get_related_artifacts') return []
  const verb = /^registry__([a-z]+)_/.exec(name)?.[1] ?? ''
  const action = name === 'extension__skill__create' ? 'published' : verb === 'get' ? 'read' : (PAST[verb] ?? verb)
  const rows = name === 'extension__skill__create' ? slotsOf(object) : [object]
  const fallbackId = artifactOf(args) ?? ''
  return rows.flatMap(row => {
    const id = field(row, 'id') || field(row, 'artifact_id') || (rows.length === 1 ? fallbackId : '')
    if (!id) return []
    return [
      {
        id,
        kind: field(row, 'kind') || id.split('_')[0] || 'artifact',
        name: field(row, 'display_name') || field(row, 'name'),
        version: releaseOf(row),
        status: field(row, 'artifact_status'),
        action,
      },
    ]
  })
}

/** The version an artifact or publish slot names: its release's label, else its own. */
function releaseOf(row: Record<string, unknown>): string {
  const releases = Array.isArray(row.current_releases) ? row.current_releases.filter(isRecord) : []
  return (releases[0] && field(releases[0], 'version_label')) || field(row, 'version_label')
}

/** Folds new touches into the list, newest first; a read never hides an earlier write. */
export function mergeTouches(list: readonly Touch[], fresh: readonly Touch[], limit = 50): Touch[] {
  let next = [...list]
  for (const touch of fresh) {
    const old = next.find(one => one.id === touch.id)
    const action = old && touch.action === 'read' && old.action !== 'read' ? old.action : touch.action
    const merged: Touch = {
      ...touch,
      name: touch.name || old?.name || '',
      version: touch.version || old?.version || '',
      status: touch.status || old?.status || '',
      action,
    }
    next = [merged, ...next.filter(one => one.id !== touch.id)]
  }
  return next.slice(0, limit)
}

/** One skill's publish, as the band shows it. */
export type PublishState = 'sending' | 'scanning' | 'published' | 'blocked' | 'unchanged' | 'failed'
export type Publish = { name: string; id: string; state: PublishState; text: string }

export const SETTLED: ReadonlySet<PublishState> = new Set(['published', 'blocked', 'unchanged', 'failed'])

/** Where one slot of a publish stands. */
function publishStep(slot: Record<string, unknown>, name: string): Publish {
  const id = field(slot, 'id') || field(slot, 'artifact_id') || field(slot, 'existing_artifact_id')
  const code = typeof slot.status_code === 'number' ? slot.status_code : 0
  const status = field(slot, 'artifact_status')
  if (code === 409) return { name, id, state: 'unchanged', text: `${name}: unchanged, identical to the current version` }
  if (code >= 400) return { name, id, state: 'failed', text: `${name}: failed, ${clip(field(slot, 'message') || field(slot, 'error') || `status ${code}`, 100)}` }
  if (status === 'blocked') return { name, id, state: 'blocked', text: `${name}: blocked by the security scan` }
  if (status === 'active' || slot.is_released === true) {
    const version = releaseOf(slot)
    return { name, id, state: 'published', text: `${name}: published${version ? ` ${version}` : ''}` }
  }
  return { name, id, state: 'scanning', text: `${name}: scanning` }
}

/** The publishes a call starts: one per skill it sends. */
export function startPublishes(args: unknown): Publish[] {
  return publishNames(args).map(name => ({ name, id: '', state: 'sending' as const, text: `${name}: sending` }))
}

/** Where each publish stands once `extension__skill__create` answered. */
export function settlePublishes(started: readonly Publish[], text: string, isError: boolean): Publish[] {
  if (isError) {
    const reason = summarizeResult(text, true).replace(/^Failed: /, '')
    return started.map(one => ({ ...one, state: 'failed' as const, text: `${one.name}: failed, ${clip(reason, 100)}` }))
  }
  const object = resultObject(text)
  const slots = object ? slotsOf(object) : []
  if (slots.length === 0 && object && isText(object.id)) slots.push(object)
  return started.map((one, i) => {
    const slot = slots[i]
    return slot ? publishStep(slot, one.name) : { ...one, state: 'scanning' as const, text: `${one.name}: sent, waiting for the scan` }
  })
}

/** A later read or release of a skill being published moves its publish on. */
export function followPublish(list: readonly Publish[], name: string, text: string, isError: boolean): Publish[] {
  if (isError || (name !== 'registry__get_artifact' && name !== 'registry__release_artifact')) return [...list]
  const object = resultObject(text)
  if (!object) return [...list]
  const id = field(object, 'id') || field(object, 'artifact_id')
  return list.map(one => (one.id !== '' && one.id === id && one.state === 'scanning' ? publishStep(object, one.name) : one))
}

/** A skill row for the picker. */
export type SkillRow = { id: string; name: string; version: string; description: string; status: string }

/** The skills a `search_artifacts` result lists. */
export function skillRows(text: string): { total: number; rows: SkillRow[] } | undefined {
  const object = resultObject(text)
  if (!object || !Array.isArray(object.items)) return undefined
  const rows = object.items.filter(isRecord).flatMap(item => {
    const id = field(item, 'id')
    if (!id) return []
    return [
      {
        id,
        name: field(item, 'display_name') || field(item, 'name') || id,
        version: field(item, 'version_label'),
        description: field(item, 'description'),
        status: field(item, 'artifact_status'),
      },
    ]
  })
  const page = isRecord(object.page) ? object.page : {}
  return { total: typeof page.total === 'number' ? page.total : rows.length, rows }
}

/** What the confirmation card shows while an archive, approve or move waits for an answer. */
export type Card = {
  /** The call being decided. */
  id: string
  title: string
  where: string
  dependents: { id: string; kind: string; rel: string }[]
  /** How many depend on it; -1 when they could not be read. */
  count: number
  consequence: string
}

/** The card for an archive, approve or move, from what the registry said about the artifact. */
export function cardOf(callId: string, name: string, artifactId: string, summary?: Summary, related?: Related): Card {
  const verb = name.replace(/^registry__/, '').replace(/_artifact$/, '')
  const what = summary?.name ? `${summary.kind || 'artifact'} "${summary.name}"` : artifactId
  return {
    id: callId,
    title: clip(`${verb[0]?.toUpperCase() ?? ''}${verb.slice(1)} ${what}${summary?.version ? ` ${summary.version}` : ''}`, 160),
    where: clip(
      [summary?.workspace ? `in ${summary.workspace}` : '', summary?.status ? summary.status.replace(/_/g, ' ') : '', artifactId].filter(Boolean).join(' · '),
      160,
    ),
    dependents: (related?.dependents ?? []).map(one => ({ id: clip(one.id, 80), kind: clip(one.kind, 40), rel: clip(one.rel, 40) })),
    count: related === undefined ? -1 : related.count,
    consequence: CONSEQUENCE[name.trim().toLowerCase()] ?? '',
  }
}
