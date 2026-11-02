import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import {
  classify,
  dependentsOf,
  describeBlastRadius,
  diagnose,
  findSessionId,
  harvestHints,
  jsonObjects,
  repairRails,
  searchRows,
  summaryOf,
} from '../hooks/registry'

const CALL = 'mcp__plugin_atlanai_atlan__mcp__call_tool'
const DESCRIBE = 'mcp__plugin_atlanai_atlan__mcp__describe_tools'
const SESSION = 'mcp_session_01m6cb39bcesgadrp1h8b69m74'

// Shapes as the live gateway returns them (trimmed), the receipt appended as a second object.
const RELATED = JSON.stringify({
  count: 2,
  incoming: [
    { from: { id: 'skill_01dep1', is_readable: true, kind: 'skill' }, rel_type: 'depends_on', relationship_status: 'active' },
    { from: { id: 'agent_01dep2', is_readable: true, kind: 'agent' }, rel_type: 'uses', relationship_status: 'active' },
  ],
  outgoing: [],
})
const ARTIFACT = JSON.stringify({
  id: 'skill_01target',
  kind: 'skill',
  display_name: 'skill-creator',
  version_label: '0.2.0',
  workspace_id: 'workspace_01ws',
  artifact_status: 'active',
})
const RECEIPT = JSON.stringify({ mcp_session_id: SESSION, next: 'Pass this id as top-level session_id on subsequent MCP calls.' })
const DESCRIBED = JSON.stringify({
  tools: [
    { name: 'registry__get_artifact', annotations: { readOnlyHint: true, openWorldHint: false } },
    { name: 'registry__patch_artifact', annotations: { readOnlyHint: false, destructiveHint: false } },
    { name: 'registry__archive_artifact', annotations: { readOnlyHint: false, destructiveHint: true } },
  ],
})

const text = (value: string) => ({ content: [{ type: 'text', text: value }], isError: false })

/** What the engine answers beneath the mod: a clock, a store, the status line and commands. */
function engine(on: On, lines: unknown[] = []) {
  mock.store(on)
  on('ui.status', (_, e) => {
    lines.push((e as { text?: unknown }).text)
    return { value: undefined }
  })
  on('command.register', () => ({ value: { command: 'atlan' } }))
  return mock.clock(on, { now: 1_800_000_000_000 })
}

const atlan = (args: string) => ({
  command: 'atlan',
  args,
  origin: { kind: 'composer' as const },
  presentation: { isFullscreen: false, columns: 100 },
})

describe('classify', () => {
  test('reads, writes and the calls that always ask', () => {
    expect(classify('registry__search_artifacts', {})).toBe('read')
    expect(classify('registry__get_file_content', {})).toBe('read')
    expect(classify('registry__create_artifact', {})).toBe('write')
    expect(classify('registry__archive_artifact', {})).toBe('destructive')
    expect(classify('registry__move_artifact', { registry__move_artifact: { destructive: false } })).toBe('destructive')
    expect(classify('slack__send_message', {})).toBe('unknown')
  })

  test('annotations decide a tool the naming does not', () => {
    expect(classify('linear__list_issues', { linear__list_issues: { readOnly: true } })).toBe('read')
    expect(classify('linear__save_issue', { linear__save_issue: {} })).toBe('write')
    expect(classify('github__delete_file', { github__delete_file: { destructive: true } })).toBe('destructive')
  })
})

describe('repairRails', () => {
  test('moves registry rails out of arguments and reuses the open session', () => {
    const { input, changes } = repairRails(
      { name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x', rationale: 'Reading it.' } },
      SESSION,
      true,
    )
    expect(input).toEqual({ name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'Reading it.', session_id: SESSION })
    expect(changes.length).toBe(2)
  })

  test("leaves another server's own rationale argument alone", () => {
    const { input } = repairRails({ name: 'review__submit', arguments: { rationale: 'LGTM' }, rationale: 'Submitting.' }, '', true)
    expect(input).toEqual({ name: 'review__submit', arguments: { rationale: 'LGTM' }, rationale: 'Submitting.' })
  })

  test('moves a gateway session id from any tool, and keeps an explicit one', () => {
    const moved = repairRails({ name: 'slack__send_message', arguments: { session_id: SESSION }, rationale: 'r' }, '', true)
    expect(moved.input.session_id).toBe(SESSION)
    const kept = repairRails({ name: 'registry__get_artifact', arguments: {}, rationale: 'r', session_id: 'mcp_session_other' }, SESSION, true)
    expect(kept.input.session_id).toBe('mcp_session_other')
  })
})

describe('parsing gateway results', () => {
  test('reads the session receipt after a result', () => {
    expect(jsonObjects(`${ARTIFACT}${RECEIPT}`).length).toBe(2)
    expect(findSessionId(`${ARTIFACT}${RECEIPT}`)).toBe(SESSION)
    expect(summaryOf(`${ARTIFACT}${RECEIPT}`)?.name).toBe('skill-creator')
  })

  test('names the blast radius of an archive', () => {
    const related = dependentsOf(RELATED)
    expect(related?.count).toBe(2)
    const reason = describeBlastRadius('registry__archive_artifact', 'skill_01target', summaryOf(ARTIFACT), related)
    expect(reason).toBe(
      'Atlan: archive skill "skill-creator" 0.2.0 in workspace_01ws. 2 artifacts depend on it: skill skill_01dep1, agent agent_01dep2. Archiving hides it from search, listings and installs for everyone.',
    )
  })

  test('learns annotations and lists search results', () => {
    expect(harvestHints(DESCRIBED).registry__archive_artifact).toEqual({ readOnly: false, destructive: true })
    const rows = searchRows(JSON.stringify({ items: [{ id: 'skill_01a', display_name: 'pii-scrub', version_label: '1.0.0', artifact_status: 'blocked' }], page: { total: 4 } }))
    expect(rows).toEqual({ total: 4, rows: ['pii-scrub 1.0.0 [blocked]  skill_01a'] })
  })

  test('diagnoses connection failures without echoing them', () => {
    expect(diagnose('Proxy refused to open a tunnel: 403 Forbidden')).toBe('unreachable')
    expect(diagnose('401 Unauthorized')).toBe('sign-in')
    expect(diagnose('MCP server "atlan" not found')).toBe('missing')
  })
})

describe('the guard', () => {
  test('lets a registry read run without a prompt', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'ask' }))
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__search_artifacts', arguments: { query: 'pii' }, rationale: 'r' } })
    expect(verdict.decision).toBe('allow')
  })

  test("keeps the engine's deny", async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'deny', reason: 'a deny rule' }))
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__get_artifact', arguments: {}, rationale: 'r' } })
    expect(verdict.decision).toBe('deny')
  })

  test('leaves another server’s unannotated tool to the session’s rules', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'ask', reason: 'engine' }))
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'slack__send_message', arguments: {}, rationale: 'r' } })
    expect(verdict).toEqual(expect.objectContaining({ decision: 'ask', reason: 'engine' }))
  })

  test('asks before an archive and names what depends on it', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', (_, e) => {
      const name = (e.args as { name?: string }).name
      return { value: text(name === 'registry__get_related_artifacts' ? RELATED : `${ARTIFACT}${RECEIPT}`) }
    })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' } })
    expect(verdict.decision).toBe('ask')
    expect(verdict.reason).toContain('2 artifacts depend on it')
  })

  test('still asks before an archive when the registry cannot be read', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', () => {
      throw new Error('offline')
    })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__move_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' } })
    expect(verdict.decision).toBe('ask')
    expect(verdict.reason).toContain('Could not read what depends on it.')
  })
})

describe('call repair', () => {
  test('moves rails out of arguments before the call runs', async ($, on) => {
    engine(on)
    let seen: Record<string, unknown> = {}
    on('tool.call', (_, e) => {
      seen = { ...e }
      return { result: 'ok', text: ARTIFACT }
    })
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x', rationale: 'Reading it.' } })
    expect(seen.rationale).toBe('Reading it.')
    expect(seen.arguments).toEqual({ artifact_id: 'skill_01x' })
  })

  test('refuses a call with no rationale, saying how to fix it', async ($, on) => {
    engine(on)
    on('tool.call', () => ({ result: 'should not run' }))
    const ran = await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' } })
    expect(String(ran.deny ?? ran.text)).toContain('rationale')
  })

  test('reuses the session a result opened on the next call', async ($, on) => {
    engine(on)
    const sessions: unknown[] = []
    on('tool.call', (_, e) => {
      sessions.push((e as { session_id?: unknown }).session_id)
      return { result: 'ok', text: `${ARTIFACT}${RECEIPT}` }
    })
    await $.tool.call({ tool: CALL, name: 'registry__search_artifacts', arguments: { query: 'x' }, rationale: 'r', objective: 'o' })
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r' })
    expect(sessions).toEqual([undefined, SESSION])
  })

  test('learns annotations from describe_tools', async ($, on) => {
    engine(on)
    on('tool.call', () => ({ result: 'ok', text: JSON.stringify({ tools: [{ name: 'linear__save_issue', annotations: { readOnlyHint: false, destructiveHint: true } }] }) }))
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', () => ({ value: text('{}') }))
    await $.tool.call({ tool: DESCRIBE, servers: ['linear'] })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'linear__save_issue', arguments: {}, rationale: 'r' } })
    expect(verdict.decision).toBe('ask')
  })
})

describe('/atlan', () => {
  test('reports a working connection', async ($, on) => {
    engine(on)
    on('mcp.call', () => ({ value: text(DESCRIBED) }))
    const ran = await $.command.run(atlan(''))
    expect(ran.text).toContain('Atlan: connected through plugin:atlanai:atlan')
  })

  test('says what to do when the network is blocked', async ($, on) => {
    engine(on)
    on('mcp.call', () => {
      throw new Error('Proxy refused to open a tunnel: 403 Forbidden')
    })
    const ran = await $.command.run(atlan('status'))
    expect(ran.text).toContain('cannot reach api.atlan.com')
  })

  test('searches the registry without a model turn', async ($, on) => {
    engine(on)
    on('mcp.call', () => ({
      value: text(`${JSON.stringify({ items: [{ id: 'skill_01a', display_name: 'pii-scrub', version_label: '1.0.0', artifact_status: 'active' }], page: { total: 1 } })}${RECEIPT}`),
    }))
    const ran = await $.command.run(atlan('search pii'))
    expect(ran.text).toBe('1 found for "pii":\n  pii-scrub 1.0.0  skill_01a')
  })
})

describe('writes and the status line', () => {
  test('a write with nobody to ask follows the session’s own rules', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow', rule: 'mcp__plugin_atlanai_atlan__mcp__call_tool' }))
    const verdict = await $.tool.check({
      tool: CALL,
      input: { name: 'registry__create_artifact', arguments: { kind: 'skill', metadata: { name: 'x' } }, rationale: 'r' },
    })
    expect(verdict.decision).toBe('allow')
  })

  test('puts a lasting connection problem on the status line, after one retry', async ($, on) => {
    const lines: unknown[] = []
    const clock = engine(on, lines)
    on('mcp.call', () => {
      throw new Error('Proxy refused to open a tunnel: 403 Forbidden')
    })
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
    await clock.settle()
    expect(lines).toEqual([])
    await clock.advance(20_000)
    await clock.settle()
    expect(lines).toEqual(['Atlan: offline, run /atlan'])
  })

  test('shows nothing when the connection works', async ($, on) => {
    const lines: unknown[] = []
    const clock = engine(on, lines)
    on('mcp.call', () => ({ value: text(DESCRIBED) }))
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
    await clock.settle()
    expect(lines).toEqual([undefined])
  })
})
