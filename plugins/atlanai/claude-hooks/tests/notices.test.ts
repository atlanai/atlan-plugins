import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import { FEEDBACK_ON_FAILURE, isExpected, isNewer, marketplaceOf, updateNotice, versionOf } from '../notices'
import { CAPABILITY, ROUTING, BULK_CAPABILITY, RECEIPT_CAPABILITY } from '../register'
import { LATEST_URL, VERSION } from '../release'

const READ = 'mcp__plugin_atlanai_atlan__mcp__call_read_tool'
const SEARCH = 'mcp__plugin_atlanai_atlan__mcp__search_tools'
const OTHER = 'mcp__plugin_linear_linear__create_issue'
const prompt = (text: string) => ({ text, wait: false, origin: { kind: 'composer' as const } })

/** The next minor release after this build's version. */
function later(): string {
  const [major, minor] = VERSION.split('.').map(Number)
  return `${major}.${(minor ?? 0) + 1}.0`
}

/** main's manifest, answering `version`; every URL fetched is recorded. */
function manifest(on: On, version: string, fetched: string[] = []) {
  on('http.fetch', (_, e) => {
    fetched.push(e.url)
    return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify({ name: 'atlanai', version }) } }
  })
  return fetched
}

/** The tool beneath the plugin: each call answers `answer` as an error, or ok. */
function tools(on: On, answer: { text: string; isError: boolean }) {
  on('tool.call', () => ({ result: answer.text, text: answer.text, isError: answer.isError }))
}

/** The engine's clock and an empty environment, which every hook here reads. */
function world(on: On, variables: Record<string, string> = {}) {
  on('tool.list', () => ({ value: [{ name: READ.replace('call_read_tool', 'call_write_tool'), description: 'Gateway runner', mcp: true }] }))
  mock.clock(on, { now: Date.UTC(2026, 10, 9, 10, 0, 0) })
  mock.env(on, variables)
}

function contexts(on: On) {
  const seen: (readonly string[] | undefined)[] = []
  on('prompt.submit', (_, e) => {
    seen.push(e.context)
    return { text: e.text, context: e.context }
  })
  return seen
}

describe('the version rules', () => {
  test('compares x.y.z releases and nothing else', () => {
    expect(isNewer('0.5.41', '0.5.40')).toBe(true)
    expect(isNewer('0.6.0', '0.5.40')).toBe(true)
    expect(isNewer('0.5.40', '0.5.40')).toBe(false)
    expect(isNewer('0.5.9', '0.5.40')).toBe(false)
    expect(isNewer('1.0.0-rc.1', '0.5.40')).toBe(false)
    expect(isNewer(undefined, '0.5.40')).toBe(false)
  })

  test('reads the version off the manifest text, or nothing', () => {
    expect(versionOf('{"version":"0.5.41"}')).toBe('0.5.41')
    expect(versionOf('{"version":"latest"}')).toBe(undefined)
    expect(versionOf('<html>404</html>')).toBe(undefined)
  })

  test('finds the marketplace from a cache install only', () => {
    expect(marketplaceOf('/home/u/.claude/plugins/cache/atlan/atlanai/0.5.40', 'atlanai')).toBe('atlan')
    expect(marketplaceOf('C:\\Users\\u\\.claude\\plugins\\cache\\atlan-prod\\atlanai\\0.5.40', 'atlanai')).toBe('atlan-prod')
    expect(marketplaceOf('/home/u/.claude/plugins/synced/org/abc', 'atlanai')).toBe(undefined)
    expect(updateNotice('atlanai', '0.5.40', '0.5.41', 'atlan')).toContain('`claude plugin update atlanai@atlan`')
    expect(updateNotice('atlanai', '0.5.40', '0.5.41', undefined)).toContain('`/plugin`')
  })

  test('reads main on the plugin repository', () => {
    expect(LATEST_URL).toBe('https://raw.githubusercontent.com/atlanai/atlan-plugins/main/plugins/atlanai/plugin.json')
  })
})

describe('the update notice', () => {
  test('rides the first prompt once when main is ahead', async ($, on) => {
    on('session.id', () => ({ value: 'session-1' }))
    world(on)
    const fetched = manifest(on, later())
    const seen = contexts(on)
    await $.prompt.submit(prompt('hello'))
    await $.prompt.submit(prompt('again'))
    expect(fetched).toEqual([LATEST_URL])
    expect(seen[0]?.[0]).toBe(CAPABILITY)
    expect(seen[0]?.[1]).toBe(ROUTING)
    expect(seen[0]?.[4]).toContain(`is at ${VERSION} and ${later()} is released`)
    expect(seen[1]).toBe(undefined)
  })

  test('says nothing when this build is the latest', async ($, on) => {
    on('session.id', () => ({ value: 'session-1' }))
    world(on)
    manifest(on, VERSION)
    const seen = contexts(on)
    await $.prompt.submit(prompt('hello'))
    expect(seen).toEqual([[CAPABILITY, ROUTING, BULK_CAPABILITY, RECEIPT_CAPABILITY]])
  })

  test('reads nothing when the user turned the check off', async ($, on) => {
    on('session.id', () => ({ value: 'session-1' }))
    world(on, { ATLAN_PLUGIN_UPDATE_CHECK: '0' })
    const fetched = manifest(on, later())
    const seen = contexts(on)
    await $.prompt.submit(prompt('hello'))
    expect(fetched).toEqual([])
    expect(seen).toEqual([[CAPABILITY, ROUTING, BULK_CAPABILITY, RECEIPT_CAPABILITY]])
  })
})

describe('the feedback offer', () => {
  test('follows the first failed Atlan call that troubleshooting may not fix, once', async ($, on) => {
    on('session.id', () => ({ value: 'session-1' }))
    world(on)
    tools(on, { text: '{"code":"invalid_arguments","message":"skills[0].files is required"}', isError: true })
    const first = await $.tool.call({ tool: READ, name: 'registry__get_artifact', arguments: {} } as never)
    const second = await $.tool.call({ tool: SEARCH, query: 'skills' } as never)
    expect((first as { context?: string[] }).context).toEqual([FEEDBACK_ON_FAILURE])
    expect((second as { context?: string[] }).context).toBe(undefined)
  })

  test('leaves retries, sign-in and conflicts to the skills', async ($, on) => {
    on('session.id', () => ({ value: 'session-1' }))
    world(on)
    tools(on, { text: 'HTTP 503 Service Unavailable', isError: true })
    const ran = await $.tool.call({ tool: READ, name: 'registry__get_artifact', arguments: {} } as never)
    expect((ran as { context?: string[] }).context).toBe(undefined)
    for (const text of ['{"code":"conflict"} 409', '401 authentication required', '{"retryable":true}', 'JSON-RPC -32003 rate_limited', 'session not found; call mcp__search_tools'])
      expect(isExpected(text)).toBe(true)
    expect(isExpected('{"code":"not_found"}')).toBe(false)
  })

  test('ignores other servers and successful calls', async ($, on) => {
    on('session.id', () => ({ value: 'session-1' }))
    world(on)
    tools(on, { text: 'boom', isError: true })
    const other = await $.tool.call({ tool: OTHER, title: 'x' } as never)
    expect((other as { context?: string[] }).context).toBe(undefined)
  })

  test('is off under DO_NOT_TRACK', async ($, on) => {
    on('session.id', () => ({ value: 'session-1' }))
    world(on, { DO_NOT_TRACK: '1' })
    tools(on, { text: '{"code":"invalid_arguments"}', isError: true })
    const ran = await $.tool.call({ tool: READ, name: 'registry__get_artifact', arguments: {} } as never)
    expect((ran as { context?: string[] }).context).toBe(undefined)
  })
})
