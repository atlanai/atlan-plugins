import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import {
  BATCH_TOOL,
  CALL_TOOL,
  CHECKED_TOOL,
  DIRECT_READ,
  GATEWAY_TOOL,
  batchCalls,
  batchEffect,
  batchEntries,
  capped,
  classify,
  dependentsOf,
  describeBlastRadius,
  describeWrite,
  diagnose,
  fallbackOf,
  releasesOf,
  findSessionId,
  gatewayNameOf,
  harvestHints,
  isStaleSession,
  jsonObjects,
  probeServers,
  repairRails,
  retryOf,
  runnerCap,
  searchRows,
  serverKeyOf,
  summaryOf,
  unwrap,
} from '../hooks/registry'

const CALL = 'mcp__plugin_atlanai_atlan__mcp__call_tool'
const DESCRIBE = 'mcp__plugin_atlanai_atlan__mcp__describe_tools'
const BATCH = 'mcp__plugin_atlanai_atlan__mcp__call_read_tools'
const GET = 'mcp__plugin_atlanai_atlan__registry__get_artifact'
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

describe('gateway tool names', () => {
  test('match under any server key, and name it', () => {
    for (const tool of ['mcp__plugin_atlanai_atlan__mcp__call_tool', 'mcp__atlan__mcp__call_tool', 'mcp__atlan-prod__mcp__call_tool'])
      expect(CALL_TOOL.test(tool)).toBe(true)
    expect(GATEWAY_TOOL.test('mcp__atlan-prod__mcp__search_tools')).toBe(true)
    expect(GATEWAY_TOOL.test('mcp__slack__send_message')).toBe(false)
    // Only the six reads search mode lists by name: a write called by name is not this mod's.
    expect(GATEWAY_TOOL.test('mcp__atlan__registry__patch_artifact')).toBe(false)
    expect(CHECKED_TOOL.test('mcp__atlan__registry__archive_artifact')).toBe(false)
    for (const tool of ['mcp__atlan__mcp__call_read_tool', 'mcp__plugin_atlanai_atlan__mcp__call_write_tool']) {
      expect(CALL_TOOL.test(tool)).toBe(true)
      expect(GATEWAY_TOOL.test(tool)).toBe(true)
    }
    expect(CALL_TOOL.test('mcp__atlan__mcp__call_other_tool')).toBe(false)
    expect(serverKeyOf('mcp__atlan-prod__mcp__call_tool')).toBe('atlan-prod')
    expect(serverKeyOf('mcp__slack__send_message')).toBeUndefined()
  })

  test('cover the batch runner and the reads listed by name (B1)', () => {
    for (const tool of [BATCH, 'mcp__atlan-prod__mcp__call_read_tools']) {
      expect(BATCH_TOOL.test(tool)).toBe(true)
      expect(GATEWAY_TOOL.test(tool)).toBe(true)
      expect(CHECKED_TOOL.test(tool)).toBe(true)
      expect(CALL_TOOL.test(tool)).toBe(false)
      expect(runnerCap(tool)).toBe('read')
    }
    for (const read of ['search_artifacts', 'get_artifact', 'list_workspaces', 'get_artifact_counts', 'get_related_artifacts', 'get_file_content']) {
      const tool = `mcp__atlan-prod__registry__${read}`
      expect(DIRECT_READ.test(tool)).toBe(true)
      expect(GATEWAY_TOOL.test(tool)).toBe(true)
      expect(CHECKED_TOOL.test(tool)).toBe(true)
      expect(gatewayNameOf(tool)).toBe(`registry__${read}`)
      expect(serverKeyOf(tool)).toBe('atlan-prod')
    }
    expect(CHECKED_TOOL.test(CALL)).toBe(true)
    expect(CHECKED_TOOL.test(DESCRIBE)).toBe(false)
    expect(gatewayNameOf(CALL)).toBe('mcp__call_tool')
    expect(gatewayNameOf('mcp__slack__registry__get_artifact_x')).toBeUndefined()
  })

  test("probe a gateway the model called under its own key first, never a plugin's twice", () => {
    expect(probeServers('atlan-prod')).toEqual(['atlan-prod', 'plugin:atlanai:atlan', 'atlan'])
    expect(probeServers('plugin_atlanai_atlan')).toEqual(['plugin:atlanai:atlan', 'atlan'])
    expect(probeServers('atlan')).toEqual(['plugin:atlanai:atlan', 'atlan'])
    expect(probeServers(undefined)).toEqual(['plugin:atlanai:atlan', 'atlan'])
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

  test("moves an app's rails out too: the gateway serves extension tools itself", () => {
    const { input } = repairRails(
      { name: 'extension__skill__create', arguments: { workspace_id: 'w', rationale: 'Publishing.', objective: 'Ship it.' } },
      SESSION,
      true,
    )
    expect(input).toEqual({
      name: 'extension__skill__create',
      arguments: { workspace_id: 'w' },
      rationale: 'Publishing.',
      objective: 'Ship it.',
      session_id: SESSION,
    })
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

describe('a batch of reads', () => {
  const calls = [
    { name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01target' } },
    { name: 'registry__get_related_artifacts', arguments: { artifact_id: 'skill_01target' } },
  ]

  test('names its calls and is judged by the worst of them', () => {
    expect(batchCalls({ calls })).toEqual([
      { name: 'registry__get_artifact', args: { artifact_id: 'skill_01target' } },
      { name: 'registry__get_related_artifacts', args: { artifact_id: 'skill_01target' } },
    ])
    expect(batchCalls({ calls: [] })).toBeUndefined()
    expect(batchCalls({ calls: 'registry__get_artifact' })).toBeUndefined()
    expect(batchCalls({})).toBeUndefined()
    expect(batchEffect(batchCalls({ calls }) ?? [], {})).toBe('read')
    // The gateway refuses a write in a batch, so a gateway write there can only read.
    expect(batchEffect([{ name: 'registry__patch_artifact' }], {})).toBe('read')
    expect(batchEffect([{ name: 'registry__get_artifact' }, { name: 'slack__send_message' }], {})).toBe('unknown')
    expect(batchEffect([{ name: 'slack__send_message' }, { name: 'registry__archive_artifact' }], {})).toBe('destructive')
    // A runner nested in a batch is judged by the call it wraps.
    const nested = batchCalls({ calls: [{ name: 'mcp__call_tool', arguments: { name: 'registry__move_artifact', arguments: {} } }] }) ?? []
    expect(batchEffect(nested, {})).toBe('destructive')
  })

  test('reads its answers in order, and the session a first batch opened', () => {
    const answer = JSON.stringify({
      results: [
        { name: 'registry__get_artifact', isError: false, content: [{ type: 'text', text: `${ARTIFACT}${RECEIPT}` }] },
        { name: 'registry__get_related_artifacts', isError: true, error: 'artifact not found' },
      ],
    })
    expect(batchEntries(answer)).toEqual([
      { name: 'registry__get_artifact', text: `${ARTIFACT}${RECEIPT}`, isError: false },
      { name: 'registry__get_related_artifacts', text: 'artifact not found', isError: true },
    ])
    expect(findSessionId(answer)).toBe(SESSION)
    // A publish answers with `results` too, but its rows carry no `isError`.
    expect(batchEntries(JSON.stringify({ results: [{ name: 'deploy-preview', status_code: 201 }] }))).toBeUndefined()
    expect(batchEntries(ARTIFACT)).toBeUndefined()
  })
})

describe('the guard', () => {
  test('lets a batch of registry reads run without a prompt (B1)', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'ask' }))
    const calls = [
      { name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01target' } },
      { name: 'registry__get_related_artifacts', arguments: { artifact_id: 'skill_01target' } },
    ]
    const verdict = await $.tool.check({ tool: BATCH, input: { calls, rationale: 'r' } })
    expect(verdict.decision).toBe('allow')
    expect(verdict.reason).toContain('2 calls')
    const ceiling = await $.tool.check({ tool: BATCH, input: { calls, rationale: 'r' }, ceiling: 'ask' })
    expect(ceiling.decision).toBe('ask')
  })

  test("leaves a batch holding another server's tool, or no calls, to the session's rules (B1)", async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'ask', reason: 'engine' }))
    const mixed = await $.tool.check({
      tool: BATCH,
      input: { calls: [{ name: 'registry__get_artifact', arguments: {} }, { name: 'slack__send_message', arguments: {} }], rationale: 'r' },
    })
    expect(mixed).toEqual({ decision: 'ask', reason: 'engine' })
    const empty = await $.tool.check({ tool: BATCH, input: { rationale: 'r' } })
    expect(empty).toEqual({ decision: 'ask', reason: 'engine' })
  })

  test('still asks for an archive named in a batch, and refuses it where nobody can answer (B1)', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    const input = { calls: [{ name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' } }], rationale: 'r' }
    const refused = await $.tool.check({ tool: BATCH, input })
    expect(refused.decision).toBe('deny')
    expect(refused.reason).toContain('archive artifact skill_01target')
    const asked = await $.tool.check({ tool: BATCH, input, approver: 'user' })
    expect(asked.decision).toBe('ask')
  })

  test('lets a registry read called by name run without a prompt (B1)', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'ask' }))
    const verdict = await $.tool.check({ tool: GET, input: { artifact_id: 'skill_01target', rationale: 'r' } })
    expect(verdict.decision).toBe('allow')
    expect(verdict.reason).toContain('registry__get_artifact')
    const denied = await $.tool.check({ tool: GET, input: { artifact_id: 'skill_01target' }, ceiling: 'deny' })
    expect(denied.decision).toBe('ask')
  })

  test('asks for a tool listed by name once the registry no longer marks it read-only (B1)', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('tool.call', () => ({ result: 'ok', text: JSON.stringify({ tools: [{ name: 'registry__get_file_content', annotations: { readOnlyHint: false } }] }) }))
    await $.tool.call({ tool: DESCRIBE, servers: ['registry'] })
    const verdict = await $.tool.check({ tool: 'mcp__plugin_atlanai_atlan__registry__get_file_content', input: { artifact_id: 'skill_01x', path: 'SKILL.md' }, approver: 'user' })
    expect(verdict.decision).toBe('ask')
  })

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

  test("makes its own lookups through the read runner, which the gateway lets only read (B8)", async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    const runners: unknown[] = []
    on('mcp.call', (_, e) => {
      runners.push(e.tool)
      const name = (e.args as { name?: string }).name
      return { value: text(name === 'registry__get_related_artifacts' ? RELATED : ARTIFACT) }
    })
    await $.tool.check({ tool: CALL, input: { name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' }, approver: 'user' })
    await $.command.run(atlan('search pii'))
    expect(runners.length).toBe(3)
    expect(new Set(runners)).toEqual(new Set(['mcp__call_read_tool']))
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

  test('waits at most 3 seconds before that retry, whatever retry_after_seconds says', async ($, on) => {
    const clock = engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    let failures = 1
    on('mcp.call', (_, e) => {
      const name = (e.args as { name?: string }).name
      if (name === 'registry__get_related_artifacts' && failures-- > 0)
        return { value: { content: [{ type: 'text', text: JSON.stringify({ code: 'downstream_unavailable', retryable: true, retry_after_seconds: 60 }) }], isError: true } }
      return { value: text(name === 'registry__get_related_artifacts' ? RELATED : `${ARTIFACT}${RECEIPT}`) }
    })
    const pending = $.tool.check({ tool: CALL, input: { name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' } })
    await clock.settle()
    await clock.advance(3_000)
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
  test('gives a batch and a read called by name the open session (B1)', async ($, on) => {
    engine(on)
    const seen: Record<string, unknown>[] = []
    on('tool.call', (_, e) => {
      seen.push({ ...e })
      return { result: 'ok', text: `${ARTIFACT}${RECEIPT}` }
    })
    await $.tool.call({ tool: CALL, name: 'registry__search_artifacts', arguments: { query: 'x' }, rationale: 'r', objective: 'o' })
    await $.tool.call({ tool: BATCH, calls: [{ name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' } }], rationale: 'r' })
    await $.tool.call({ tool: GET, artifact_id: 'skill_01x', rationale: 'r' })
    expect(seen.map(e => e.session_id)).toEqual([undefined, SESSION, SESSION])
    expect(seen[2]?.artifact_id).toBe('skill_01x')
  })

  test('learns the session a first batch opened, from inside its answers (B1)', async ($, on) => {
    engine(on)
    const sessions: unknown[] = []
    on('tool.call', (_, e) => {
      sessions.push((e as { session_id?: unknown }).session_id)
      return { result: 'ok', text: JSON.stringify({ results: [{ name: 'registry__get_artifact', isError: false, content: [{ type: 'text', text: `${ARTIFACT}${RECEIPT}` }] }] }) }
    })
    await $.tool.call({ tool: BATCH, calls: [{ name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' } }], rationale: 'r' })
    await $.tool.call({ tool: GET, artifact_id: 'skill_01x', rationale: 'r' })
    expect(sessions).toEqual([undefined, SESSION])
  })

  test('refuses a batch with no rationale, and leaves a read called by name to the gateway (B1)', async ($, on) => {
    engine(on)
    on('tool.call', () => ({ result: 'ran', text: ARTIFACT }))
    const batch = await $.tool.call({ tool: BATCH, calls: [{ name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' } }] })
    expect(String(batch.deny)).toContain('beside `calls`')
    const direct = await $.tool.call({ tool: GET, artifact_id: 'skill_01x' })
    expect(direct.deny).toBeUndefined()
    expect(direct.result).toBe('ran')
  })

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

  test('leaves a lost skill publish to the publish helper, which knows when its idempotency_key applies', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('tool.call', () => ({ result: '503 Service Unavailable', text: 'HTTP 503 Service Unavailable', isError: true }))
    const skills = [{ name: 'demo', workspace_id: 'workspace_01ws', files: { 'SKILL.md': '---\nname: demo\n---\n' } }]
    const ran = await $.tool.call({ tool: CALL, name: 'extension__skill__create', arguments: { skills }, rationale: 'r' })
    expect((ran.context ?? []).some(note => note.includes('idempotency_key'))).toBe(false)
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

describe('a release and its roll-back', () => {
  test('names the release a withdrawal takes down, and what it does to everyone (B2)', () => {
    const asked = describeWrite('registry__withdraw_release', { release_id: 'rel_01shipped', reason: 'broken' })
    expect(asked).toContain('withdraw release rel_01shipped.')
    expect(asked).toContain('back to the previous release')
    expect(describeBlastRadius('registry__release_artifact', 'skill_01target', summaryOf(ARTIFACT), { count: 0, dependents: [] })).toContain('Releasing makes this version')
    // A plain write keeps its one sentence.
    expect(describeWrite('registry__patch_artifact', { artifact_id: 'skill_01x' })).toBe('Atlan registry write: patch artifact skill_01x.')
  })

  test('asks before a withdrawal with the release in the question (B2)', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('tool.call', () => ({ result: 'ok', text: JSON.stringify({ tools: [{ name: 'registry__withdraw_release', annotations: { readOnlyHint: false, destructiveHint: true } }] }) }))
    await $.tool.call({ tool: DESCRIBE, servers: ['registry'] })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__withdraw_release', arguments: { release_id: 'rel_01shipped' }, rationale: 'r' }, approver: 'user' })
    expect(verdict.decision).toBe('ask')
    expect(verdict.reason).toContain('rel_01shipped')
    expect(verdict.reason).toContain('cannot be undone')
  })
})

describe('the roll-back card (C3)', () => {
  const RELEASES = JSON.stringify({
    items: [
      { release_id: 'rel_01v110', artifact_id: 'skill_01target', channel: 'latest', version_label: '1.1.0', version_ordinal: 3, release_status: 'released', is_current: true },
      { release_id: 'rel_01v105', artifact_id: 'skill_01target', channel: 'latest', version_label: '1.0.5', version_ordinal: 2, release_status: 'withdrawn', is_current: false, withdrawn_at: '2026-10-01T00:00:00Z' },
      { release_id: 'rel_01v100', artifact_id: 'skill_01target', channel: 'latest', version_label: '1.0.0', version_ordinal: 1, release_status: 'released', is_current: false },
      { release_id: 'rel_01beta', channel: 'beta', version_label: '1.1.0', version_ordinal: 3, is_current: true },
    ],
  })

  test('reads the releases a listing names, and where a withdrawal sends the channel', () => {
    const releases = releasesOf(RELEASES, 'skill_01target')
    expect(releases.map(one => [one.id, one.artifact, one.isWithdrawn])).toEqual([
      ['rel_01v110', 'skill_01target', false],
      ['rel_01v105', 'skill_01target', true],
      ['rel_01v100', 'skill_01target', false],
      ['rel_01beta', 'skill_01target', false],
    ])
    // The withdrawn 1.0.5 is passed over.
    expect(fallbackOf(releases[0]!, releases)).toBe('"latest" goes back to 1.0.0, if that version is still active.')
    expect(fallbackOf(releases[2]!, releases)).toContain('not the current release')
    expect(fallbackOf(releases[3]!, releases)).toContain('may be left serving nothing')
    expect(releasesOf(ARTIFACT, 'skill_01target')).toEqual([])
    expect(releasesOf(JSON.stringify({ items: [{ release_id: 'rel_01orphan' }] }), '')).toEqual([])
  })

  test('names the artifact, its dependents and the fallback for a release the session listed', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('tool.call', () => ({ result: 'ok', text: RELEASES }))
    on('mcp.call', (_, e) => {
      const call = e.args as { name?: string; arguments?: { artifact_id?: string } }
      expect(call.arguments?.artifact_id).toBe('skill_01target')
      return { value: text(call.name === 'registry__get_related_artifacts' ? RELATED : ARTIFACT) }
    })
    await $.tool.call({ tool: 'mcp__plugin_atlanai_atlan__mcp__call_read_tool', name: 'registry__list_releases', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__withdraw_release', arguments: { release_id: 'rel_01v110', reason: 'broken' }, rationale: 'r' }, approver: 'user' })
    expect(verdict.decision).toBe('ask')
    expect(verdict.reason).toContain('withdraw release 1.1.0 on "latest" of skill "skill-creator"')
    expect(verdict.reason).toContain('goes back to 1.0.0')
    expect(verdict.reason).toContain('2 artifacts depend on it')
    expect(verdict.reason).toContain('cannot be undone')
  })

  test('learns releases from a batch too, and refuses a withdrawal nobody can confirm', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('tool.call', () => ({ result: 'ok', text: JSON.stringify({ results: [{ name: 'registry__list_releases', isError: false, content: [{ type: 'text', text: RELEASES }] }] }) }))
    on('mcp.call', (_, e) => ({ value: text((e.args as { name?: string }).name === 'registry__get_related_artifacts' ? RELATED : ARTIFACT) }))
    await $.tool.call({ tool: BATCH, calls: [{ name: 'registry__list_releases', arguments: { artifact_id: 'skill_01target' } }], rationale: 'r' })
    const verdict = await $.tool.check({ tool: CALL, input: { name: 'registry__withdraw_release', arguments: { release_id: 'rel_01v110' }, rationale: 'r' } })
    expect(verdict.decision).toBe('deny')
    expect(verdict.reason).toContain('of skill "skill-creator"')
  })

  test('a release and a withdrawal ask before any annotation is known, and through any runner', async ($, on) => {
    engine(on)
    on('tool.check', () => ({ decision: 'allow' }))
    on('mcp.call', (_, e) => ({ value: text((e.args as { name?: string }).name === 'registry__get_related_artifacts' ? RELATED : ARTIFACT) }))
    expect(classify('registry__release_artifact', {})).toBe('destructive')
    expect(classify('registry__withdraw_release', { registry__withdraw_release: { destructive: false } })).toBe('destructive')
    const release = await $.tool.check({
      tool: 'mcp__plugin_atlanai_atlan__mcp__call_write_tool',
      input: { name: 'registry__release_artifact', arguments: { artifact_id: 'skill_01target', channel: 'stable' }, rationale: 'r' },
      approver: 'user',
    })
    expect(release.decision).toBe('ask')
    expect(release.reason).toContain('release skill "skill-creator" 0.2.0')
    expect(release.reason).toContain('Releasing makes this version')
  })
})

describe('runner caps', () => {
  test('the read runner caps a gateway tool at read, the write runner at write', () => {
    expect(runnerCap('mcp__atlan__mcp__call_read_tool')).toBe('read')
    expect(runnerCap('mcp__atlan__mcp__call_write_tool')).toBe('write')
    expect(runnerCap('mcp__atlan__mcp__call_tool')).toBeUndefined()
    expect(capped('write', 'read', 'registry__create_artifact')).toBe('read')
    expect(capped('destructive', 'write', 'registry__delete_artifact')).toBe('write')
    expect(capped('destructive', undefined, 'registry__delete_artifact')).toBe('destructive')
  })

  test('another server stays unknown, and archive, approve and move still ask', () => {
    expect(capped('unknown', 'read', 'slack__send_message')).toBe('unknown')
    expect(capped('destructive', 'read', 'registry__archive_artifact')).toBe('destructive')
    expect(capped('destructive', 'write', 'registry__move_artifact')).toBe('destructive')
  })

  test('a nested runner is unwrapped like call_tool', () => {
    expect(unwrap('mcp__call_read_tool', { name: 'mcp__call_tool', arguments: { name: 'registry__get_artifact', arguments: {} } }).name)
      .toBe('registry__get_artifact')
  })
})
