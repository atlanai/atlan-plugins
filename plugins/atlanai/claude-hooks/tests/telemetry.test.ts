import type { EngineInterface } from 'claude-code'
import { describe, expect, test } from 'claude-code/testing'

import { FLUSH_MS, MAX_BATCH, MAX_EVENTS, compose, createTelemetry, failureReason, isOn, joinOf, namespaceOf, operationOf, osOf, flush, refusalOf, releasedIn, runnerOf, sessionKey, track, type Config } from '../telemetry'
import { SCHEMA, TELEMETRY } from '../telemetry-config'

const ENDPOINT = 'https://telemetry.example/v1/batch'
const INSTALL = '0b0e7a52-7d0e-4c1b-9a3e-2f6f1c9d4e10'
const ROOT = '/plugins/atlanai'
// A stand-in key, built here so no key-shaped string is committed.
const KEY = 'test' + 'key' + '12345678'
const ON: Config = { ...TELEMETRY, endpoint: ENDPOINT, write_key: KEY }
const OFF: Config = { ...TELEMETRY, write_key: '' }
const GATEWAY_SESSION = 'mcp_session_01JABCDEFGHJKMNPQRSTVWXYZ0'
const COMMON = { harness: 'claude_code', os: 'macos', install_id: INSTALL, app_version: TELEMETRY.version }
const AT = '2026-11-25T10:00:00.000Z'
const MESSAGE = '11111111-2222-4333-8444-555555555555'

type Sent = { url: string; body: { batch: { event: string; anonymousId: string; properties: Record<string, unknown>; context: { ip: string } }[]; writeKey?: string } }

/**
 * The engine as the sender sees it, in memory: an environment, the plugin's own manifest, a store,
 * and a clock whose timers the test fires. Everything the sender asks for is recorded.
 */
function world(options: { variables?: Record<string, string>; files?: Record<string, string>; store?: Record<string, unknown>; url?: string; name?: string } = {}) {
  const variables: Record<string, string> = { HOME: '/Users/someone', ...options.variables }
  const files: Record<string, string> = {
    [`${ROOT}/.mcp.json`]: JSON.stringify({ mcpServers: { atlan: { type: 'http', url: options.url ?? TELEMETRY.mcp_url } } }),
    ...options.files,
  }
  const store = new Map<string, unknown>(Object.entries(options.store ?? {}))
  const sent: Sent[] = []
  const asked: string[] = []
  const timers: { ms: number; fn: () => void }[] = []
  const read = async (path: string) => {
    asked.push(path)
    if (!(path in files)) throw new Error(`ENOENT: ${path}`)
    return files[path] as string
  }
  const $ = {
    plugin: { name: options.name ?? TELEMETRY.plugin, root: ROOT },
    env: { get: async (name: string) => (asked.push(`env:${name}`), variables[name]) },
    fs: { stat: async (path: string) => ({ kind: 'file', size: (await read(path)).length }), read },
    store: { get: async (key: string) => store.get(key), set: async (key: string, value: unknown) => void store.set(key, value) },
    session: { id: async () => 'session-1' },
    clock: { after: (ms: number, fn: () => void) => (timers.push({ ms, fn }), { cancel: () => undefined }) },
    http: {
      fetch: async (url: string, init?: { body?: string }) => {
        sent.push({ url, body: JSON.parse(String(init?.body)) })
        return { status: 200, ok: true, headers: {}, text: '{"success":true}' }
      },
    },
  } as unknown as EngineInterface
  return { $, sent, asked, timers, store }
}

describe('the event schema', () => {
  test('writes a Segment track message in the dashboard’s grammar', () => {
    const message = compose('skill_invoked', { skill: 'atlanai:atlan-continue' }, { ...COMMON, session: 'abcdef0123456789' }, ON, AT, MESSAGE)
    expect(message).toEqual({
      type: 'track',
      event: 'claude_plugin_skill_invoked',
      anonymousId: INSTALL,
      messageId: MESSAGE,
      timestamp: AT,
      properties: {
        category: 'claude_plugin',
        object: 'skill',
        action: 'invoked',
        app_version: TELEMETRY.version,
        plugin: TELEMETRY.plugin,
        harness: 'claude_code',
        os: 'macos',
        install_id: INSTALL,
        session: 'abcdef0123456789',
        skill: 'atlanai:atlan-continue',
      },
      context: { ip: '0.0.0.0', library: { name: 'atlan-plugin', version: TELEMETRY.version } },
    })
  })

  test('drops a skill or command this build does not ship', () => {
    expect(compose('skill_invoked', { skill: 'acme-internal:quarterly-forecast' }, COMMON, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('skill_invoked', { skill: 'atlanai-copy:atlan-continue' }, COMMON, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('command_run', { command: 'compact' }, COMMON, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('command_run', { command: 'atlanai:continue' }, COMMON, ON, AT, MESSAGE)?.event).toBe('claude_plugin_command_run')
  })

  test('drops an event, a value or a property outside the schema', () => {
    expect(compose('prompt_submitted', { text: 'hello' }, COMMON, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('publish_refused', { operation: 'skill_create', reason: '/Users/someone/secret.pem' }, COMMON, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('publish_refused', { operation: 'skill_create' }, COMMON, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('tool_call_failed', { runner: 'call_tool', namespace: 'registry', reason: 'HTTP 500 at /work/x' }, COMMON, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('tool_call_failed', { runner: 'call_tool', namespace: 'registry', reason: 'auth', mcp_session_id: 'my notes about the merger' }, COMMON, ON, AT, MESSAGE)).toBe(undefined)
    const kept = compose('publish_prepared', { operation: 'skill_create', path: '/work/demo', files: ['SKILL.md'] }, COMMON, ON, AT, MESSAGE)
    expect(Object.keys((kept as { properties: object }).properties).sort()).toEqual(['action', 'app_version', 'category', 'harness', 'install_id', 'object', 'operation', 'os', 'plugin'])
  })

  test('drops an event another harness sends, and a malformed identity', () => {
    expect(compose('skill_invoked', { skill: 'atlanai:atlan-continue' }, { ...COMMON, harness: 'codex' }, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('install_active', { first_seen: true }, { ...COMMON, install_id: 'someone@example.com' }, ON, AT, MESSAGE)).toBe(undefined)
    expect(compose('install_active', { first_seen: true }, { ...COMMON, session: 'session-1' }, ON, AT, MESSAGE)?.properties).not.toHaveProperty('session')
  })

  test('names every event object_action, with typed properties only', () => {
    for (const [name, event] of Object.entries(SCHEMA.events)) {
      expect(name).toMatch(/^[a-z]+_[a-z_]+$/)
      for (const spec of Object.values(event.props) as { type: string; of?: string }[]) {
        expect(['boolean', 'version', 'enum', 'listed', 'mcp_session']).toContain(spec.type)
        if (spec.type === 'enum') expect(Object.keys(SCHEMA.enums)).toContain(spec.of)
        if (spec.type === 'listed') expect(['skills', 'commands']).toContain(spec.of)
      }
    }
  })
})

describe('what a hook reads off a call', () => {
  test('folds a refusal to its first rule', () => {
    expect(refusalOf('demo/.env:1:1: credential-file: looks like a credential file; leave it out\n1 files, 9 bytes: 1 problem(s)', 'skill_create')).toBe('credential-file')
    expect(refusalOf('report.md:3:9: credential: matches the aws-access-key pattern\nNothing was sent.', 'output')).toBe('credential')
    expect(refusalOf('SKILL.md:1:1: frontmatter-description: `description` is over 1024 characters', 'skill_create')).toBe('frontmatter')
    expect(refusalOf('SKILL.md:4:2: zero-width: U+200B is invisible here; delete it', 'skill_create')).toBe('hidden-text')
    expect(refusalOf('too-many-files: 200 files; inline `files` take at most 128 a call', 'skill_create')).toBe('too-many-files')
    expect(refusalOf('The local publication batch was refused. Nothing was sent.', 'bulk_create')).toBe('local-batch')
    expect(refusalOf('The receipt destination could not be prepared. Nothing was sent.', 'bulk_create')).toBe('receipt')
    expect(refusalOf('something else', 'skill_create')).toBe('other')
  })

  test('folds an error to a kind, a tool to its helper and whose tool it ran', () => {
    expect(failureReason('401 authentication required')).toBe('auth')
    expect(failureReason('{"status":403,"code":"forbidden"}')).toBe('permission_denied')
    expect(failureReason('{"code":"not_found"}')).toBe('not_found')
    expect(failureReason('JSON-RPC -32003 rate_limited')).toBe('rate_limited')
    expect(failureReason('HTTP 503 Service Unavailable')).toBe('server_error')
    expect(failureReason('upstream timed out')).toBe('timeout')
    expect(failureReason('{"code":"invalid_arguments"}')).toBe('client_error')
    expect(failureReason('boom')).toBe('unknown')
    expect(runnerOf('mcp__plugin_atlanai_atlan__mcp__call_read_tool')).toBe('call_read_tool')
    expect(runnerOf('mcp__plugin_atlanai_atlan__registry__search_artifacts')).toBe('other')
    expect(namespaceOf('registry__get_artifact')).toBe('registry')
    expect(namespaceOf('extension__skill__create')).toBe('extension')
    expect(namespaceOf('acme_crm__list_accounts')).toBe('other')
    expect(operationOf('extension__skill__create')).toBe('skill_create')
    expect(operationOf('registry__create_artifacts')).toBe('bulk_create')
    expect(operationOf('registry__publish_output')).toBe('output')
    expect(operationOf('registry__get_artifact')).toBe(undefined)
  })

  test('carries the gateway session as the join to who made the call, only when it reads as one', () => {
    expect(joinOf(GATEWAY_SESSION)).toEqual({ mcp_session_id: GATEWAY_SESSION })
    for (const other of [undefined, 42, '', 'session-1', 'mcp_session_', 'mcp_session_01J/../x', 'someone@example.com']) expect(joinOf(other)).toEqual({})
    const message = compose('tool_call_failed', { runner: 'call_read_tool', namespace: 'registry', reason: 'not_found', ...joinOf(GATEWAY_SESSION) }, COMMON, ON, AT, MESSAGE)
    expect((message as { properties: Record<string, unknown> }).properties.mcp_session_id).toBe(GATEWAY_SESSION)
  })

  test('reads an update notice of this build only, and the machine’s family', async () => {
    expect(releasedIn('The atlanai plugin is at 0.5.1 and 0.6.0 is released. Say it once.', '0.5.1')).toEqual({ from_version: '0.5.1', to_version: '0.6.0', forced: false })
    expect(releasedIn('The atlanai plugin is at 0.5.1 and 0.6.0 is released.', '0.5.2')).toBe(undefined)
    expect(osOf({ HOME: '/Users/someone' })).toBe('macos')
    expect(osOf({ HOME: '/home/someone' })).toBe('linux')
    expect(osOf({ OS: 'Windows_NT', HOME: 'C:\\Users\\someone' })).toBe('windows')
    expect(osOf({})).toBe('unknown')
    expect(await sessionKey(INSTALL, 'session-1')).toMatch(/^[0-9a-f]{16}$/)
    expect(await sessionKey(INSTALL, 'session-1')).not.toBe(await sessionKey(INSTALL, 'session-2'))
  })
})

describe('the sender', () => {
  test('is on exactly when the build carries a write key, as the dashboard is', () => {
    expect(isOn(ON)).toBe(true)
    expect(isOn(OFF)).toBe(false)
    expect(isOn({ ...ON, endpoint: 'http://telemetry.example/v1/batch' })).toBe(false)
    expect(isOn({ ...ON, endpoint: '' })).toBe(false)
    expect(isOn({ ...ON, write_key: 'not a key' })).toBe(false)
    expect(TELEMETRY.endpoint).toBe('https://api.segment.io/v1/batch')
  })

  test('with no write key it asks for nothing and sends nothing', async () => {
    const { $, sent, asked, timers } = world()
    const telemetry = createTelemetry(OFF)
    track($, telemetry, 'skill_invoked', { skill: 'atlanai:atlan-continue' })
    await flush($, telemetry)
    expect(sent).toEqual([])
    expect(asked).toEqual([])
    expect(timers).toEqual([])
  })

  test('queues events and sends them together from one timer, with a random install id and no address', async () => {
    const { $, sent, timers } = world()
    const telemetry = createTelemetry(ON)
    track($, telemetry, 'install_active', { surface: 'terminal', interactive: true })
    track($, telemetry, 'skill_invoked', { skill: 'atlanai:atlan-continue' })
    track($, telemetry, 'skill_invoked', { skill: 'acme-internal:quarterly-forecast' })
    track($, telemetry, 'prompt_submitted', { text: 'hello' })
    expect(sent).toEqual([])
    expect(timers.map(timer => timer.ms)).toEqual([FLUSH_MS])
    await flush($, telemetry)
    expect(sent.length).toBe(1)
    expect(sent[0]?.url).toBe(ENDPOINT)
    const batch = sent[0]!.body.batch
    expect(batch.map(message => message.event)).toEqual(['claude_plugin_install_active', 'claude_plugin_skill_invoked'])
    expect(batch[0]?.properties).toEqual(expect.objectContaining({ first_seen: true, surface: 'terminal', interactive: true, harness: 'claude_code', os: 'macos' }))
    expect(batch[0]?.anonymousId).toMatch(/^[0-9a-f-]{36}$/)
    expect(batch[1]?.anonymousId).toBe(batch[0]?.anonymousId)
    expect(batch[1]?.properties.session).toMatch(/^[0-9a-f]{16}$/)
    expect(batch[0]?.context.ip).toBe('0.0.0.0')
    expect(sent[0]?.body.writeKey).toBe(KEY)
    const wire = JSON.stringify(sent[0]?.body)
    for (const leak of ['/Users/someone', 'session-1', 'acme-internal', 'hello']) expect(wire).not.toContain(leak)
  })

  test('reports an install active once per UTC day, and keeps its id', async () => {
    const { $, sent, store } = world({ store: { 'telemetry.install_id': INSTALL, 'telemetry.active_day': '2026-11-24' } })
    const telemetry = createTelemetry(ON)
    track($, telemetry, 'install_active', { surface: 'desktop', interactive: true })
    await flush($, telemetry)
    track($, telemetry, 'install_active', { surface: 'desktop', interactive: true })
    await flush($, telemetry)
    expect(sent.length).toBe(1)
    expect(sent[0]?.body.batch[0]?.anonymousId).toBe(INSTALL)
    expect(sent[0]?.body.batch[0]?.properties.first_seen).toBe(false)
    expect(store.get('telemetry.active_day')).toBe(new Date().toISOString().slice(0, 10))
  })

  test('is off under each switch a user or an organization can set, and stores nothing', async () => {
    for (const variables of [{ DO_NOT_TRACK: '1' }, { DISABLE_TELEMETRY: 'true' }, { CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: '1' }, { ATLAN_PLUGIN_TELEMETRY: 'off' }, { ATLAN_PLUGIN_TELEMETRY: '0' }, { CI: 'true' }]) {
      const { $, sent, store } = world({ variables })
      const telemetry = createTelemetry(ON)
      track($, telemetry, 'install_active', { surface: 'terminal', interactive: true })
      await flush($, telemetry)
      expect(sent).toEqual([])
      expect(store.size).toBe(0)
    }
  })

  test('is off when the opt-out file is there', async () => {
    const { $, sent, store } = world({ files: { '/Users/someone/.config/atlan-plugin/telemetry-off': '' } })
    const telemetry = createTelemetry(ON)
    track($, telemetry, 'skill_invoked', { skill: 'atlanai:atlan-continue' })
    await flush($, telemetry)
    expect(sent).toEqual([])
    expect(store.size).toBe(0)
  })

  test('is off in a copy another environment serves, by its server or its name', async () => {
    for (const options of [{ url: 'https://api.dev.example/mcp' }, { name: 'atlanai-copy' }]) {
      const { $, sent, store } = world(options)
      const telemetry = createTelemetry(ON)
      track($, telemetry, 'skill_invoked', { skill: 'atlanai:atlan-continue' })
      await flush($, telemetry)
      expect(sent).toEqual([])
      expect(store.size).toBe(0)
    }
  })

  test('stops at a fixed number of events a process', async () => {
    const { $, sent } = world()
    const telemetry = createTelemetry(ON)
    for (let i = 0; i < 500; i++) track($, telemetry, 'tool_call_failed', { runner: 'call_tool', namespace: 'registry', reason: 'server_error' })
    for (let i = 0; i < 20; i++) await flush($, telemetry)
    expect(sent.map(request => request.body.batch.length)).toEqual([MAX_BATCH, MAX_BATCH, MAX_BATCH, MAX_BATCH, MAX_BATCH])
    expect(sent.length * MAX_BATCH).toBe(MAX_EVENTS)
  })

  test('never throws into the hook, whatever the engine does', async () => {
    const broken = new Proxy({}, { get: () => { throw new Error('no engine') } }) as unknown as EngineInterface
    const telemetry = createTelemetry(ON)
    track(broken, telemetry, 'skill_invoked', { skill: 'atlanai:atlan-continue' })
    await flush(broken, telemetry)
    const { $, sent } = world()
    ;($ as unknown as { http: { fetch: () => Promise<never> } }).http.fetch = async () => { throw new Error('no network') }
    track($, telemetry, 'skill_invoked', { skill: 'atlanai:atlan-continue' })
    await flush($, telemetry)
    expect(sent).toEqual([])
  })
})
