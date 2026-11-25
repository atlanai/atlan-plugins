// Usage telemetry for the core's hooks module, for Claude Code only. command-hooks/telemetry.py is
// the same for Codex, Cursor and Copilot, and both are held to command-hooks/telemetry_events.json
// (SCHEMA here, written by the build): an event or property that file does not list is never
// sent, and a value that does not fit its type drops the whole event. So no path, prompt, tool
// argument, result or error text can leave, whatever a call site passes.
//
// Nothing is sent, and nothing is read or stored, unless every one of these holds:
//
// - `[telemetry] write_key` is set in plugin.toml (TELEMETRY.write_key, written by the build), as
//   the dashboard's analytics load only with a Segment write key;
// - none of DO_NOT_TRACK, DISABLE_TELEMETRY, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC is 1 or
//   true, ATLAN_PLUGIN_TELEMETRY is not 0, false or off, and CI is unset;
// - the file `telemetry-off` is absent from `$XDG_CONFIG_HOME/atlan-plugin` (`~/.config/atlan-plugin`);
// - this copy of the plugin is the released one: its name and MCP URL are the build's own, so a
//   copy another environment serves stays silent.
//
// Events wait in memory and leave together two seconds later, from a timer and never from the
// hook that raised them: a hook neither waits on the network nor fails on it.

import type { EngineInterface } from 'claude-code'

import { isFlagOn } from './notices'
import { SCHEMA, TELEMETRY } from './telemetry-config'

type Dollar = EngineInterface
export type Config = {
  readonly endpoint: string
  readonly write_key: string
  readonly plugin: string
  readonly version: string
  readonly mcp_url: string
  readonly skills: readonly string[]
  readonly commands: readonly string[]
}
export type Props = Readonly<Record<string, unknown>>
type Spec = { readonly type: string; readonly of?: string; readonly optional?: boolean }
type EventSpec = { readonly hosts: readonly string[]; readonly props: Readonly<Record<string, Spec>> }
type Common = { harness: string; os: string; install_id: string; app_version: string; session?: string }
type Message = Record<string, unknown>

export const HARNESS = 'claude_code'
/** How long events wait before they leave together. */
export const FLUSH_MS = 2000
/** The most events one process sends: a retry loop cannot become a stream. */
export const MAX_EVENTS = 200
/** The most events one request carries; the rest wait for the next. */
export const MAX_BATCH = 40
const MAX_BODY_BYTES = 32 * 1024
const STORE_INSTALL = 'telemetry.install_id'
const STORE_DAY = 'telemetry.active_day'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const VERSION = /^\d+\.\d+\.\d+$/
const SESSION = /^[0-9a-f]{16}$/
const MCP_SESSION = /^mcp_session_[0-9A-Za-z]{8,64}$/
const WRITE_KEY = /^[A-Za-z0-9]{8,64}$/
const RELEASED = /is at (\d+\.\d+\.\d+) and (\d+\.\d+\.\d+) is released/
const RULE = /(?:^|:\s)([a-z]+(?:-[a-z]+)*):\s/g
const OUTPUT = /^registry__publish_output$|^extension__[a-z0-9_]+__publish_output$/

const ENUMS: Readonly<Record<string, readonly string[]>> = SCHEMA.enums
const EVENTS: Readonly<Record<string, EventSpec>> = SCHEMA.events

/** check_bundle.py's rule names, folded to the schema's `refusal` values. */
const RULE_CLASS: Readonly<Record<string, string>> = {
  ...Object.fromEntries(
    ['files-from', 'symbolic-link', 'credential-folder', 'credential-file', 'credential', 'unsafe-path', 'not-a-file', 'too-many-files', 'too-large', 'missing-skill-md', 'frontmatter', 'not-text'].map(rule => [rule, rule]),
  ),
  'frontmatter-name': 'frontmatter',
  'frontmatter-description': 'frontmatter',
  'frontmatter-yaml': 'frontmatter',
  'no-body': 'frontmatter',
  ...Object.fromEntries(
    ['tag-character', 'variation-selector', 'zero-width-joiner', 'direction-control', 'zero-width', 'line-separator', 'control-character'].map(rule => [rule, 'hidden-text']),
  ),
}

/** On whenever the build carries a write key and an HTTPS endpoint, as the dashboard's is. */
export const isOn = (config: Config): boolean => config.endpoint.startsWith('https://') && WRITE_KEY.test(config.write_key)

/** The gateway's own session id when a call names one that reads as one: the join to its records. */
export function joinOf(session: unknown): { mcp_session_id?: string } {
  return typeof session === 'string' && MCP_SESSION.test(session) ? { mcp_session_id: session } : {}
}

function fits(value: unknown, spec: Spec, config: Config): boolean {
  if (spec.type === 'boolean') return typeof value === 'boolean'
  if (spec.type === 'version') return typeof value === 'string' && VERSION.test(value)
  if (spec.type === 'enum') return typeof value === 'string' && (ENUMS[spec.of ?? ''] ?? []).includes(value)
  if (spec.type === 'mcp_session') return typeof value === 'string' && MCP_SESSION.test(value)
  if (spec.type === 'listed') {
    const list = spec.of === 'skills' ? config.skills : spec.of === 'commands' ? config.commands : []
    return typeof value === 'string' && list.includes(value)
  }
  return false
}

/** One Segment `track` message, or undefined when the event or any value is outside the schema. */
export function compose(name: string, props: Props, common: Common, config: Config, at: string, messageId: string): Message | undefined {
  const event = EVENTS[name]
  if (!event || !event.hosts.includes(common.harness)) return undefined
  if (!UUID.test(common.install_id) || !VERSION.test(common.app_version)) return undefined
  if (!(ENUMS.harness ?? []).includes(common.harness) || !(ENUMS.os ?? []).includes(common.os)) return undefined
  const kept: Record<string, unknown> = {}
  for (const [key, spec] of Object.entries(event.props)) {
    const value = props[key]
    if (value === undefined || value === null) {
      if (spec.optional) continue
      return undefined
    }
    if (!fits(value, spec, config)) return undefined
    kept[key] = value
  }
  const sep = name.indexOf('_')
  return {
    type: 'track',
    event: `${SCHEMA.category}_${name}`,
    anonymousId: common.install_id,
    messageId,
    timestamp: at,
    properties: {
      category: SCHEMA.category,
      object: sep === -1 ? name : name.slice(0, sep),
      action: sep === -1 ? '' : name.slice(sep + 1),
      app_version: common.app_version,
      plugin: config.plugin,
      harness: common.harness,
      os: common.os,
      install_id: common.install_id,
      ...(common.session && SESSION.test(common.session) ? { session: common.session } : {}),
      ...kept,
    },
    // Segment records the sender's address unless the message names one.
    context: { ip: '0.0.0.0', library: { name: 'atlan-plugin', version: common.app_version } },
  }
}

/** An error's text as one of the schema's `failure` values. The text itself goes nowhere. */
export function failureReason(text: string): string {
  if (/\b401\b|authentication required|needs login|unauthori[sz]ed/i.test(text)) return 'auth'
  if (/\b403\b|forbidden|permission[ _]denied|not[ _]allowed/i.test(text)) return 'permission_denied'
  if (/\b404\b|not[ _]found/i.test(text)) return 'not_found'
  if (/\b409\b|conflict/i.test(text)) return 'conflict'
  if (/\b429\b|rate.?limit/i.test(text)) return 'rate_limited'
  if (/timed?[ _]?out|\b504\b/i.test(text)) return 'timeout'
  if (/\b5\d\d\b|bad gateway|service unavailable|downstream_unavailable/i.test(text)) return 'server_error'
  if (/\b4\d\d\b|invalid|-3260[0-3]/i.test(text)) return 'client_error'
  return 'unknown'
}

export function operationOf(name: unknown): 'skill_create' | 'bulk_create' | 'output' | undefined {
  if (name === 'extension__skill__create') return 'skill_create'
  if (name === 'registry__create_artifacts') return 'bulk_create'
  return typeof name === 'string' && OUTPUT.test(name) ? 'output' : undefined
}

/** Why the helper held a call back, as one of the schema's `refusal` values. */
export function refusalOf(text: string, operation: string): string {
  const first = text.split('\n', 1)[0] ?? ''
  for (const match of first.matchAll(RULE)) {
    const folded = RULE_CLASS[match[1] ?? '']
    if (folded) return folded
  }
  if (/\breceipt\b/i.test(text)) return 'receipt'
  return operation === 'bulk_create' ? 'local-batch' : 'other'
}

/** Which gateway helper a tool name ends in. */
export function runnerOf(tool: string): string {
  const name = /__mcp__([a-z_]+)$/.exec(tool)?.[1] ?? ''
  return (ENUMS.runner ?? []).includes(name) ? name : 'other'
}

/** Whose tool the gateway was asked to run. The tool's own name is never sent. */
export function namespaceOf(name: unknown): string {
  if (typeof name !== 'string') return 'other'
  return name.startsWith('registry__') ? 'registry' : name.startsWith('extension__') ? 'extension' : 'other'
}

/** The versions an update notice names, when it is this build's own. */
export function releasedIn(notice: string, installed: string): { from_version: string; to_version: string; forced: false } | undefined {
  const match = RELEASED.exec(notice)
  return match && match[1] === installed ? { from_version: match[1], to_version: match[2] as string, forced: false } : undefined
}

export function osOf(variables: { OS?: string; HOME?: string }): string {
  if (variables.OS === 'Windows_NT') return 'windows'
  if (!variables.HOME) return 'unknown'
  return variables.HOME.startsWith('/Users/') ? 'macos' : variables.HOME.startsWith('/') ? 'linux' : 'unknown'
}

/** Events of one session group together, and the harness's own session id is never sent. */
export async function sessionKey(installId: string, session: string): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${installId}${session}`)))
  return Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('').slice(0, 16)
}

type Ready = { installId: string; os: string }
type Queued = { name: string; props: Props; at: number; session: Promise<string> }

/** One process's sender: where it sends, what waits to go, and the consent and identity it settled. */
export type Telemetry = {
  readonly config: Config
  readonly queue: Queued[]
  ready?: Promise<Ready | undefined>
  isWaiting: boolean
  taken: number
}

export function createTelemetry(config: Config = TELEMETRY): Telemetry {
  return { config, queue: [], isWaiting: false, taken: 0 }
}

/** A variable that cannot be read is unset. Each is read by its literal name, so the engine can list them. */
const quiet = (read: Promise<string | undefined>): Promise<string | undefined> => read.catch(() => undefined)

/** Consent and identity, settled once a process. undefined: say nothing. */
async function prepare($: Dollar, config: Config): Promise<Ready | undefined> {
  if (isFlagOn(await quiet($.env.get('DO_NOT_TRACK'))) || isFlagOn(await quiet($.env.get('DISABLE_TELEMETRY')))) return undefined
  if (isFlagOn(await quiet($.env.get('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC')))) return undefined
  if (/^(?:0|false|off)$/i.test(((await quiet($.env.get('ATLAN_PLUGIN_TELEMETRY'))) ?? '').trim())) return undefined
  if (await quiet($.env.get('CI'))) return undefined
  const home = (await quiet($.env.get('HOME'))) ?? (await quiet($.env.get('USERPROFILE')))
  const base = (await quiet($.env.get('XDG_CONFIG_HOME'))) || (home ? `${home}/.config` : undefined)
  if (base && (await $.fs.stat(`${base}/atlan-plugin/telemetry-off`).then(() => true, () => false))) return undefined
  if ($.plugin.name !== config.plugin) return undefined
  const manifest: unknown = JSON.parse(await $.fs.read(`${$.plugin.root}/.mcp.json`))
  const servers = typeof manifest === 'object' && manifest !== null ? (manifest as { mcpServers?: unknown }).mcpServers : undefined
  const urls = typeof servers === 'object' && servers !== null ? Object.values(servers).map(server => (server as { url?: unknown } | null)?.url) : []
  if (!urls.includes(config.mcp_url)) return undefined
  let installId = await $.store.get(STORE_INSTALL)
  if (typeof installId !== 'string' || !UUID.test(installId)) {
    installId = crypto.randomUUID()
    await $.store.set(STORE_INSTALL, installId)
  }
  return { installId: installId as string, os: osOf({ OS: await quiet($.env.get('OS')), HOME: home }) }
}

/** Send what waits, as one batch. A batch that cannot be sent is dropped. */
export async function flush($: Dollar, telemetry: Telemetry): Promise<void> {
  const { config } = telemetry
  if (!isOn(config)) return
  const events = telemetry.queue.splice(0, MAX_BATCH)
  if (events.length === 0) return
  try {
    if (telemetry.queue.length > 0) wait($, telemetry)
    telemetry.ready ??= prepare($, config).catch(() => undefined)
    const identity = await telemetry.ready
    if (!identity) return
    const batch: Message[] = []
    for (const event of events) {
      let props = event.props
      if (event.name === 'install_active') {
        // At most once per UTC day; the first report from this install says so.
        const today = new Date(event.at).toISOString().slice(0, 10)
        const last = await $.store.get(STORE_DAY)
        if (last === today) continue
        await $.store.set(STORE_DAY, today)
        props = { ...props, first_seen: last === undefined }
      }
      const session = await event.session
      const common: Common = {
        harness: HARNESS,
        os: identity.os,
        install_id: identity.installId,
        app_version: config.version,
        ...(session ? { session: await sessionKey(identity.installId, session) } : {}),
      }
      const message = compose(event.name, props, common, config, new Date(event.at).toISOString(), crypto.randomUUID())
      if (message) batch.push(message)
    }
    if (batch.length === 0) return
    const body = JSON.stringify({ batch, writeKey: config.write_key })
    if (new TextEncoder().encode(body).length > MAX_BODY_BYTES) return
    await $.http.fetch(config.endpoint, { method: 'POST', headers: { 'content-type': 'application/json' }, body })
  } catch {
    /* Dropped. */
  }
}

/** Start the one timer that sends what waits, unless it is already running. */
function wait($: Dollar, telemetry: Telemetry): void {
  if (telemetry.isWaiting) return
  telemetry.isWaiting = true
  $.clock.after(FLUSH_MS, () => {
    telemetry.isWaiting = false
    void flush($, telemetry)
  })
}

/** Queue one event. Never throws, never waits, and does nothing while telemetry is off. */
export function track($: Dollar, telemetry: Telemetry, name: string, props: Props = {}): void {
  if (!isOn(telemetry.config) || telemetry.taken >= MAX_EVENTS || !EVENTS[name]) return
  try {
    telemetry.taken += 1
    telemetry.queue.push({ name, props, at: Date.now(), session: $.session.id().catch(() => '') })
    wait($, telemetry)
  } catch {
    /* Telemetry never changes what a hook does. */
  }
}

/** Note a call the publish helper held back; a call it has no name for is not reported. */
export function trackRefusal($: Dollar, telemetry: Telemetry, name: unknown, why: string, session?: unknown): void {
  const operation = operationOf(name)
  if (operation) track($, telemetry, 'publish_refused', { operation, reason: refusalOf(why, operation), ...joinOf(session) })
}
