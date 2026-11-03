import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import {
  classify,
  dependentsOf,
  describeBlastRadius,
  diagnose,
  findSessionId,
  harvestHints,
  isStaleSession,
  jsonObjects,
  repairRails,
  retryOf,
  searchRows,
  summaryOf,
  unwrap,
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

  test("another server's read-only hint does not make its tool a read (X2)", () => {
    expect(classify('linear__list_issues', { linear__list_issues: { readOnly: true } })).toBe('unknown')
    expect(classify('linear__save_issue', { linear__save_issue: {} })).toBe('unknown')
    // A destructive hint can only add a question, so it still counts.
    expect(classify('github__delete_file', { github__delete_file: { destructive: true } })).toBe('destructive')
    // The gateway's own tools keep their annotations.
    expect(classify('extension__skill__create', { extension__skill__create: { readOnly: false } })).toBe('write')
    expect(classify('registry__get_artifact', { registry__get_artifact: { readOnly: true } })).toBe('read')
  })

  test('case and space do not slip a call past the names that always ask (X1b)', () => {
    expect(classify(' Registry__Archive_Artifact ', {})).toBe('destructive')
    expect(classify('REGISTRY__MOVE_ARTIFACT', { registry__move_artifact: { destructive: false } })).toBe('destructive')
    expect(classify('Registry__Create_Artifact', {})).toBe('write')
  })

  test('a nested call_tool is judged by the call it wraps (X1d)', () => {
    const inner = unwrap('mcp__call_tool', { name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' } })
    expect(inner).toEqual({ name: 'registry__archive_artifact', args: { artifact_id: 'skill_01target' } })
    expect(classify(inner.name, {})).toBe('destructive')
    const twice = unwrap('mcp__call_tool', { name: 'MCP__CALL_TOOL', arguments: { name: 'registry__patch_artifact', arguments: {} } })
    expect(classify(twice.name, {})).toBe('write')
    const stringed = unwrap('mcp__call_tool', JSON.stringify({ name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' } }))
    expect(classify(stringed.name, {})).toBe('destructive')
    expect(classify('mcp__search_tools', {})).toBe('read')
    expect(classify('mcp__initialise', {})).toBe('write')
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
    expect(diagnose('HTTP 502 Bad Gateway')).toBe('degraded')
  })

  test("clears a session only on the gateway's own refusal of it (X3b)", () => {
    expect(isStaleSession('session not found; call `mcp__search_tools` without `session_id`, with an `objective`, to start a new session')).toBe(true)
    expect(isStaleSession('the requested session is unavailable; call `mcp__initialise` to open one')).toBe(true)
    expect(isStaleSession(`artifact not found${RECEIPT}`)).toBe(false)
    expect(isStaleSession('session closed by the workspace owner; file not found')).toBe(false)
  })

  test('tells a transient failure from a refusal', () => {
    expect(retryOf('HTTP 503 Service Unavailable')).toEqual({ retryable: true, afterSeconds: 2 })
    expect(retryOf(JSON.stringify({ code: 'downstream_unavailable', retryable: true, retry_after_seconds: 7 }))).toEqual({ retryable: true, afterSeconds: 7 })
    expect(retryOf(JSON.stringify({ code: 'downstream_unavailable', retryable: false }))).toBeUndefined()
    expect(retryOf('400 Bad Request: `query` is required')).toBeUndefined()
    expect(retryOf('401 Unauthorized')).toBeUndefined()
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

  // A test's check has no approver, as under `claude -p`: an archive, move or approve is refused
  // there, with the same blast radius a person would be asked about.
  test('refuses an archive nobody can confirm and names what depends on it', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', (_, e) => {
      const name = (e.args as { name?: string }).name
      return { value: text(name === 'registry__get_related_artifacts' ? RELATED : `${ARTIFACT}${RECEIPT}`) }
    })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' } })
    expect(verdict.decision).toBe('deny')
    expect(verdict.reason).toContain('2 artifacts depend on it')
    expect(verdict.reason).toContain('Nobody can confirm it here')
  })

  test('reads what depends on an artifact again after a transient failure', async ($, on) => {
    const clock = engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    let failures = 1
    on('mcp.call', (_, e) => {
      const name = (e.args as { name?: string }).name
      if (name === 'registry__get_related_artifacts' && failures-- > 0)
        return { value: { content: [{ type: 'text', text: 'HTTP 503 Service Unavailable' }], isError: true } }
      return { value: text(name === 'registry__get_related_artifacts' ? RELATED : `${ARTIFACT}${RECEIPT}`) }
    })
    const pending = $.tool.check({ tool: CALL, input: { name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' } })
    await clock.settle()
    await clock.advance(2_000)
    await clock.settle()
    const verdict = await pending
    expect(verdict.reason).toContain('2 artifacts depend on it')
  })

  test("does not let another server's read-only hint allow its tool (X2)", async ($, on) => {
    engine(on)
    on('tool.call', () => ({ result: 'ok', text: JSON.stringify({ tools: [{ name: 'slack__send_message', annotations: { readOnlyHint: true } }] }) }))
    on('tool.check', () => ({ decision: 'ask', reason: 'engine' }))
    await $.tool.call({ tool: DESCRIBE, servers: ['slack'] })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'slack__send_message', arguments: {}, rationale: 'r' } })
    expect(verdict).toEqual(expect.objectContaining({ decision: 'ask', reason: 'engine' }))
  })

  test('guards an archive spelled in another case (X1b)', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', () => ({ value: text('{}') }))
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'Registry__Archive_Artifact ', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' } })
    expect(verdict.decision).toBe('deny')
  })

  test('guards an archive nested inside call_tool (X1d)', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', (_, e) => {
      const name = (e.args as { name?: string }).name
      return { value: text(name === 'registry__get_related_artifacts' ? RELATED : `${ARTIFACT}${RECEIPT}`) }
    })
    const verdict = await $.tool.check({
      tool: CALL,
      input: { name: 'mcp__call_tool', arguments: { name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' } }, rationale: 'r' },
    })
    expect(verdict.decision).toBe('deny')
    expect(verdict.reason).toContain('2 artifacts depend on it')
  })

  test('still guards an archive when the registry cannot be read', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', () => {
      throw new Error('offline')
    })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__move_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' } })
    expect(verdict.decision).toBe('deny')
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

  test('keeps the session when a tool says its artifact was not found (X3b)', async ($, on) => {
    engine(on)
    const sessions: unknown[] = []
    const answers = [
      { result: 'ok', text: `${ARTIFACT}${RECEIPT}` },
      { result: 'artifact not found', text: `artifact not found${RECEIPT}`, isError: true },
      { result: 'ok', text: ARTIFACT },
    ]
    on('tool.call', (_, e) => {
      sessions.push((e as { session_id?: unknown }).session_id)
      return answers.shift() ?? { result: 'ok', text: ARTIFACT }
    })
    await $.tool.call({ tool: CALL, name: 'registry__search_artifacts', arguments: { query: 'x' }, rationale: 'r', objective: 'o' })
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01gone' }, rationale: 'r' })
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r' })
    expect(sessions).toEqual([undefined, SESSION, SESSION])
  })

  test("does not hand the mod's own session to the model's calls (X6)", async ($, on) => {
    engine(on)
    on('mcp.call', () => ({ value: text(`${JSON.stringify({ items: [], page: { total: 0 } })}${RECEIPT}`) }))
    const sessions: unknown[] = []
    on('tool.call', (_, e) => {
      sessions.push((e as { session_id?: unknown }).session_id)
      return { result: 'ok', text: ARTIFACT }
    })
    await $.command.run(atlan('search pii'))
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r', objective: 'o' })
    expect(sessions).toEqual([undefined])
  })

  test('tells the model to repeat a read after a transient failure, and never a write', async ($, on) => {
    const lines: unknown[] = []
    engine(on, lines)
    on('tool.check', () => ({ decision: 'allow' }))
    on('tool.call', () => ({ result: '503 Service Unavailable', text: 'HTTP 503 Service Unavailable', isError: true }))
    const read = await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r' })
    expect(read.context?.at(-1)).toContain('safe to repeat')
    expect(lines.at(-1)).toBe('Atlan: degraded, retrying reads')
    const write = await $.tool.call({ tool: CALL, name: 'registry__patch_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r' })
    expect(write.context?.at(-1)).toContain('may already have run')
  })

  test('learns annotations from describe_tools', async ($, on) => {
    engine(on)
    on('tool.call', () => ({ result: 'ok', text: JSON.stringify({ tools: [{ name: 'linear__save_issue', annotations: { readOnlyHint: false, destructiveHint: true } }] }) }))
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', () => ({ value: text('{}') }))
    await $.tool.call({ tool: DESCRIBE, servers: ['linear'] })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'linear__save_issue', arguments: {}, rationale: 'r' } })
    expect(verdict.decision).toBe('deny')
  })
})

describe('/atlan', () => {
  test('reports a working connection', async ($, on) => {
    engine(on)
    on('mcp.call', () => ({ value: text(DESCRIBED) }))
    const ran = await $.command.run(atlan(''))
    expect(ran.text).toContain('Atlan: connected through plugin:atlanai:atlan')
  })

  test('reports a call that failed as a failure, not as a server that did not answer (X5a)', async ($, on) => {
    engine(on)
    // A hook that throws is skipped, so the call fails beneath it at once: no timeout passed.
    on('mcp.call', () => {
      throw new Error('401 Unauthorized')
    })
    const ran = await $.command.run(atlan('status'))
    expect(ran.text).not.toContain('did not answer')
    expect(ran.text).toContain('Atlan: error')
  })

  test('reports a refused sign-in as sign-in', async ($, on) => {
    engine(on)
    on('mcp.call', () => ({ value: { content: [{ type: 'text', text: '401 Unauthorized' }], isError: true } }))
    const ran = await $.command.run(atlan('status'))
    expect(ran.text).toContain('Atlan: sign-in')
  })

  test('says what to do when the network is blocked', async ($, on) => {
    engine(on)
    on('mcp.call', () => ({ value: { content: [{ type: 'text', text: 'Proxy refused to open a tunnel: 403 Forbidden' }], isError: true } }))
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
    on('mcp.call', () => ({ value: { content: [{ type: 'text', text: 'Proxy refused to open a tunnel: 403 Forbidden' }], isError: true } }))
    on('session.start', (_, e) => ({ cwd: e.cwd }))
    await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
    await clock.settle()
    expect(lines).toEqual([])
    await clock.advance(20_000)
    await clock.settle()
    expect(lines).toEqual(['Atlan: offline, run /atlan'])
  })

  test('shows a degraded gateway on the status line during an outage, and clears it after', async ($, on) => {
    const lines: unknown[] = []
    engine(on, lines)
    on('tool.check', () => ({ decision: 'allow' }))
    const answers = [
      { result: 'bad gateway', text: 'HTTP 502 Bad Gateway', isError: true },
      { result: 'ok', text: ARTIFACT },
    ]
    on('tool.call', () => answers.shift() ?? { result: 'ok', text: ARTIFACT })
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r' })
    expect(lines.at(-1)).toBe('Atlan: degraded, retrying reads')
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r' })
    expect(lines.at(-1)).toBeUndefined()
  })

  // The engine runs the permission check inside the call, under the call's own id, so a write the
  // guard checked never reads as skipped. (A plugin seated above this one that answers the check
  // without passing it on is what turns the warning on; a test cannot seat one there.)
  test('stays quiet about a write the guard checked (L-02)', async ($, on) => {
    const lines: unknown[] = []
    engine(on, lines)
    on('tool.check', () => ({ decision: 'allow' }))
    on('tool.call', () => ({ result: 'ok', text: ARTIFACT }))
    await $.tool.call({ tool: CALL, name: 'registry__patch_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r' })
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' }, rationale: 'r' })
    expect(lines.filter(Boolean)).toEqual([])
    const ran = await $.command.run(atlan('status'))
    expect(ran.text).not.toContain('Another plugin answered')
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
