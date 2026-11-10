import type { On, ToolGroupCall } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import {
  cardOf,
  describeCall,
  followPublish,
  mergeTouches,
  settlePublishes,
  skillRows,
  startPublishes,
  summarizeResult,
  touchesOf,
} from '../hooks/view'

const CALL = 'mcp__plugin_atlanai_atlan__mcp__call_tool'
const SEARCH_TOOLS = 'mcp__plugin_atlanai_atlan__mcp__search_tools'
const BATCH = 'mcp__plugin_atlanai_atlan__mcp__call_read_tools'
const GET = 'mcp__plugin_atlanai_atlan__registry__get_artifact'
const SESSION = 'mcp_session_01m6cb39bcesgadrp1h8b69m74'
const RECEIPT = JSON.stringify({ 'com.atlan/receipt': { mcp_session_id: SESSION, next: 'Pass this id as top-level session_id.' } })
const PLUGIN = 'atlanai-mods'

// Shapes as the live gateway returns them (trimmed).
const ARTIFACT = {
  id: 'skill_01target',
  kind: 'skill',
  display_name: 'skill-creator',
  version_label: '0.2.0',
  workspace_id: 'workspace_01ws',
  artifact_status: 'active',
}
const SEARCH = {
  items: [
    { id: 'skill_01a', kind: 'skill', display_name: 'pii-scrub', version_label: '1.0.0', artifact_status: 'active', description: 'Masks PII in a table before it is shared.' },
    { id: 'skill_01b', kind: 'skill', display_name: 'pii-audit', version_label: '0.3.0', artifact_status: 'pending_review', description: 'Lists columns that hold PII.' },
  ],
  page: { limit: 2, offset: 0, total: 7 },
}
const PUBLISH_ARGS = { skills: [{ name: 'deploy-preview', workspace_id: 'workspace_01ws', files: { 'SKILL.md': '---\nname: deploy-preview\n---\n' } }] }

const text = (value: string) => ({ content: [{ type: 'text', text: value }], isError: false })

/** What the engine answers beneath the mod for the UI calls these tests make. */
function engine(on: On, seen: { toasts: string[]; opened: string[]; copied: string[]; filled: string[]; closed: string[] }) {
  mock.store(on)
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', (_, e) => {
    seen.toasts.push(String((e as { text?: unknown }).text))
    return { value: undefined }
  })
  on('ui.open', (_, e) => {
    seen.opened.push(String((e as { id?: unknown }).id))
    return { value: { isPlaced: true } }
  })
  on('ui.close', (_, e) => {
    seen.closed.push(String((e as { id?: unknown }).id))
    return { value: undefined }
  })
  on('ui.copy', (_, e) => {
    seen.copied.push(String((e as { text?: unknown }).text))
    return { value: { isCopied: true } }
  })
  on('prompt.fill', (_, e) => {
    seen.filled.push(e.text)
    return { isFilled: true }
  })
  on('command.register', () => ({ value: { command: 'atlan' } }))
  return mock.clock(on, { now: 1_800_000_000_000 })
}

const fresh = () => ({ toasts: [] as string[], opened: [] as string[], copied: [] as string[], filled: [] as string[], closed: [] as string[] })

const scroll = { offset: 0, bodyRows: 20 }
const pane = (requestId: string) =>
  ({
    plugin: PLUGIN,
    surface: 'terminal',
    component: 'Pane',
    requestId,
    props: { title: 'Atlan', isFocused: false, bodyColumns: 60, placement: 'dock', scroll, view: {} },
  }) as const

const atlan = (args: string) => ({
  command: 'atlan',
  args,
  origin: { kind: 'composer' as const },
  presentation: { isFullscreen: false, columns: 160 },
})

describe('what a call did, in words', () => {
  test('names registry calls, publishes and other servers', () => {
    expect(describeCall(CALL, { name: 'registry__search_artifacts', arguments: { query: 'churn', kind: 'skill' } })).toBe('searched skills for "churn"')
    expect(describeCall(CALL, { name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' } })).toBe('read skill_01x')
    expect(describeCall(CALL, { name: 'registry__list_workspaces', arguments: {} })).toBe('listed workspaces')
    expect(describeCall(CALL, { name: 'registry__archive_artifact', arguments: { artifact_id: 'skill_01x' } })).toBe('archived skill_01x')
    expect(describeCall(CALL, { name: 'registry__create_artifact', arguments: { kind: 'output', metadata: { name: 'Q3 report' } } })).toBe('created output "Q3 report"')
    expect(describeCall(CALL, { name: 'extension__skill__create', arguments: PUBLISH_ARGS })).toBe('published skill deploy-preview')
    expect(describeCall(CALL, { name: 'slack__send_message', arguments: {} })).toBe('slack · send message')
    expect(describeCall(SEARCH_TOOLS, { query: 'linear issues' })).toBe('searched tools for "linear issues"')
  })

  test('names a batch of reads and a read called by name (B1)', () => {
    const calls = [
      { name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01x' } },
      { name: 'registry__get_related_artifacts', arguments: { artifact_id: 'skill_01x' } },
    ]
    expect(describeCall(BATCH, { calls, rationale: 'r' })).toBe('ran 2 reads: read skill_01x; read what relates to skill_01x')
    expect(describeCall(BATCH, { calls: calls.slice(0, 1) })).toBe('read skill_01x')
    expect(describeCall(BATCH, {})).toBe('ran a batch of reads')
    expect(describeCall(GET, { artifact_id: 'skill_01x', rationale: 'r' })).toBe('read skill_01x')
    expect(describeCall('mcp__atlan__registry__search_artifacts', { query: 'churn', kind: 'skill' })).toBe('searched skills for "churn"')
    const answer = JSON.stringify({
      results: [
        { name: 'registry__get_artifact', isError: false, content: [{ type: 'text', text: `${JSON.stringify(ARTIFACT)}${RECEIPT}` }] },
        { name: 'registry__get_related_artifacts', isError: true, error: 'artifact not found' },
      ],
    })
    expect(summarizeResult(answer, false)).toBe('1 of 2 reads answered: skill skill-creator 0.2.0; Failed: artifact not found')
  })

  test('reads a share, a roll-back and a scan rerun in the past tense (B9)', () => {
    expect(describeCall(CALL, { name: 'registry__publish_output', arguments: { output: { name: 'Q3 churn report' }, file: { content: 'x' } } })).toBe('shared output "Q3 churn report"')
    expect(describeCall(CALL, { name: 'registry__publish_output', arguments: {} })).toBe('shared an output')
    expect(describeCall(CALL, { name: 'registry__withdraw_release', arguments: { release_id: 'rel_01shipped' } })).toBe('withdrew release rel_01shipped')
    expect(describeCall(CALL, { name: 'registry__retrigger_artifact_action', arguments: { artifact_id: 'skill_01x', action_name: 'scan' } })).toBe('reran scan on skill_01x')
    const shared = touchesOf('registry__publish_output', {}, JSON.stringify({ output: { id: 'output_01q3', kind: 'output', name: 'Q3 churn report', version_ordinal: 1 }, file: { id: 'file_01q3' }, created: true }), false)
    expect(shared.map(one => [one.id, one.kind, one.name, one.action])).toEqual([['output_01q3', 'output', 'Q3 churn report', 'shared']])
  })

  test('sums up what came back', () => {
    expect(summarizeResult(`${JSON.stringify(SEARCH)}${RECEIPT}`, false)).toBe('7 found: pii-scrub 1.0.0, pii-audit 0.3.0 (pending review), and 5 more')
    expect(summarizeResult(`${JSON.stringify(ARTIFACT)}${RECEIPT}`, false)).toBe('skill skill-creator 0.2.0')
    expect(summarizeResult(JSON.stringify({ items: [], page: { total: 0 } }), false)).toBe('Nothing found.')
    expect(summarizeResult(JSON.stringify({ message: 'artifact not found' }), true)).toBe('Failed: artifact not found')
    expect(summarizeResult('', false)).toBe('Done.')
  })
})

describe('what the session touched', () => {
  test('records reads and writes, not search hits, and keeps a write over a later read', () => {
    const read = touchesOf('registry__get_artifact', { artifact_id: 'skill_01target' }, JSON.stringify(ARTIFACT), false)
    expect(read).toEqual([{ id: 'skill_01target', kind: 'skill', name: 'skill-creator', version: '0.2.0', status: 'active', action: 'read' }])
    expect(touchesOf('registry__search_artifacts', { query: 'x' }, JSON.stringify(SEARCH), false)).toEqual([])
    const patched = touchesOf('registry__patch_artifact', { artifact_id: 'skill_01target' }, JSON.stringify({ ...ARTIFACT, version_label: '0.3.0' }), false)
    const list = mergeTouches(mergeTouches([], patched), read)
    expect(list.map(one => [one.action, one.version])).toEqual([['updated', '0.2.0']])
  })
})

describe('the publish band', () => {
  test('follows a publish from sending to published, scanning or blocked', () => {
    const started = startPublishes(PUBLISH_ARGS)
    expect(started.map(one => one.text)).toEqual(['deploy-preview: sending'])
    const released = { skills: [{ status_code: 201, id: 'skill_01new', name: 'deploy-preview', artifact_status: 'active', current_releases: [{ version_label: '0.1.0', channel: 'latest' }] }] }
    expect(settlePublishes(started, JSON.stringify(released), false)[0]?.text).toBe('deploy-preview: published 0.1.0')
    const pending = settlePublishes(started, JSON.stringify({ skills: [{ status_code: 201, id: 'skill_01new', artifact_status: 'pending_review' }] }), false)
    expect(pending[0]?.state).toBe('scanning')
    const later = followPublish(pending, 'registry__get_artifact', JSON.stringify({ id: 'skill_01new', artifact_status: 'blocked' }), false)
    expect(later[0]?.text).toBe('deploy-preview: blocked by the security scan')
    expect(settlePublishes(started, JSON.stringify({ skills: [{ status_code: 409, existing_artifact_id: 'skill_01old' }] }), false)[0]?.state).toBe('unchanged')
    expect(settlePublishes(started, 'workspace not found', true)[0]?.text).toBe('deploy-preview: failed, workspace not found')
  })
})

describe('the skill picker', () => {
  test('reads skill rows from a search', () => {
    expect(skillRows(`${JSON.stringify(SEARCH)}${RECEIPT}`)?.rows[0]).toEqual({
      id: 'skill_01a',
      name: 'pii-scrub',
      version: '1.0.0',
      description: 'Masks PII in a table before it is shared.',
      status: 'active',
    })
  })
})

describe('the transcript row', () => {
  const row = {
    tool_use_id: 'toolu_01',
    tool: CALL,
    input: { name: 'registry__search_artifacts', arguments: { query: 'churn', kind: 'skill' }, rationale: 'r' },
    isRunning: false,
    isErrored: false,
    isInterrupted: false,
  }

  test('reads as one line of what the call did, on every surface', async ($, on) => {
    engine(on, fresh())
    for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: 'ToolUse', requestId: 'toolu_01', props: row })
      expect((await ui.find({ type: 'Text', text: /searched/ }))?.text).toBe(' · searched skills for "churn"')
      await ui.unmount()
    }
  })

  test('sums up the result under it, and leaves ctrl+o and other tools to the engine', async ($, on) => {
    engine(on, fresh())
    on('ui.render', ($, e) => {
      const { Text } = $.ui.resolve(e)
      return <Text>engine</Text>
    })
    const result = await $.ui.mount({
      plugin: PLUGIN,
      surface: 'terminal',
      component: 'ToolResult',
      requestId: 'toolu_01',
      props: { tool_use_id: 'toolu_01', tool: CALL, output: [{ type: 'text', text: `${JSON.stringify(SEARCH)}${RECEIPT}` }], isErrored: false },
    })
    expect((await result.find({ type: 'Text' }))?.text).toBe('  ⎿ 7 found: pii-scrub 1.0.0, pii-audit 0.3.0 (pending review), and 5 more')
    await result.unmount()

    const expanded = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', component: 'ToolUse', requestId: 'toolu_01', props: { ...row, isExpanded: true } })
    expect((await expanded.find({ type: 'Text' }))?.text).toBe('engine')
    await expanded.unmount()

    const bash = await $.ui.mount({ plugin: PLUGIN, surface: 'terminal', component: 'ToolUse', requestId: 'toolu_02', props: { ...row, tool: 'Bash', input: { command: 'ls' } } })
    expect((await bash.find({ type: 'Text' }))?.text).toBe('engine')
    await bash.unmount()
  })
})

describe('the Atlan pane', () => {
  test('lists what a batch and a read called by name read (B1)', async ($, on) => {
    engine(on, fresh())
    const other = { ...ARTIFACT, id: 'skill_01other', display_name: 'pii-scrub', version_label: '1.0.0' }
    on('tool.call', (_, e) =>
      (e as { tool: string }).tool === BATCH
        ? { result: 'ok', text: JSON.stringify({ results: [{ name: 'registry__get_artifact', isError: false, content: [{ type: 'text', text: JSON.stringify(ARTIFACT) }] }] }) }
        : { result: 'ok', text: JSON.stringify(other) },
    )
    await $.tool.call({ tool: BATCH, calls: [{ name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01target' } }], rationale: 'r' })
    await $.tool.call({ tool: GET, artifact_id: 'skill_01other', rationale: 'r' })
    const ui = await $.ui.mount(pane('atlan'))
    expect(await ui.find({ type: 'Text', text: /skill-creator/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /pii-scrub/ })).toBeDefined()
    await ui.unmount()
  })

  test('lists what the session changed, opens itself at the first write, and copies an id', async ($, on) => {
    const seen = fresh()
    engine(on, seen)
    on('tool.call', (_, e) => {
      const name = (e as { name?: string }).name
      const body = name === 'registry__patch_artifact' ? { ...ARTIFACT, version_label: '0.3.0' } : ARTIFACT
      return { result: 'ok', text: `${JSON.stringify(body)}${RECEIPT}` }
    })
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' })
    expect(seen.opened).toEqual([])
    await $.tool.call({ tool: CALL, name: 'registry__patch_artifact', arguments: { artifact_id: 'skill_01target' }, rationale: 'r' })
    expect(seen.opened).toEqual(['atlan'])

    const ui = await $.ui.mount(pane('atlan'))
    expect((await ui.find({ type: 'Text', text: /skill-creator/ }))?.text).toBe('updated skill skill-creator 0.3.0')
    await ui.press({ key: 'copy-skill_01target' })
    expect(seen.copied).toEqual(['skill_01target'])
    await ui.unmount()
  })

  test('builds the card an archive shows while it waits for an answer', () => {
    const card = cardOf('toolu_09', 'registry__archive_artifact', 'skill_01target', { name: 'skill-creator', kind: 'skill', version: '0.2.0', workspace: 'workspace_01ws', status: 'active' }, {
      count: 1,
      dependents: [{ id: 'agent_01dep', kind: 'agent', rel: 'uses_skill' }],
    })
    expect(card).toEqual({
      id: 'toolu_09',
      title: 'Archive skill "skill-creator" 0.2.0',
      where: 'in workspace_01ws · active · skill_01target',
      dependents: [{ id: 'agent_01dep', kind: 'agent', rel: 'uses_skill' }],
      count: 1,
      consequence: 'Archiving hides it from search, listings and installs for everyone.',
    })
    expect(cardOf('toolu_10', 'registry__move_artifact', 'skill_01x').count).toBe(-1)
  })
})

describe('the publish band, drawn', () => {
  const band = {
    plugin: PLUGIN,
    surface: 'terminal',
    component: 'AbovePrompt',
    requestId: 'above-prompt',
    props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 9 }, view: {} },
  } as const

  test('shows a publish to its end, toasts it, and clears at the next request', async ($, on) => {
    const seen = fresh()
    engine(on, seen)
    on('ui.render', ($, e) => {
      const { Text } = $.ui.resolve(e)
      return <Text>engine</Text>
    })
    on('prompt.submit', () => ({ text: 'ok' }))
    const released = { skills: [{ status_code: 201, id: 'skill_01new', name: 'deploy-preview', artifact_status: 'active', current_releases: [{ version_label: '0.1.0' }] }] }
    on('tool.call', () => ({ result: 'ok', text: `${JSON.stringify(released)}${RECEIPT}` }))
    await $.tool.call({ tool: CALL, name: 'extension__skill__create', arguments: PUBLISH_ARGS, rationale: 'r' })
    expect(seen.toasts).toEqual(['Atlan: deploy-preview: published 0.1.0'])

    const ui = await $.ui.mount(band)
    expect((await ui.find({ type: 'Text', text: /deploy-preview/ }))?.text).toBe('✓ deploy-preview: published 0.1.0')
    await ui.unmount()

    await $.prompt.submit({ text: 'next request', wait: false, origin: { kind: 'composer' } })
    const after = await $.ui.mount(band)
    expect((await after.find({ type: 'Text' }))?.text).toBe('engine')
    await after.unmount()
  })
})

describe('/atlan skills', () => {
  test('opens the picker, searches, and puts the chosen skill in the prompt', async ($, on) => {
    const seen = fresh()
    engine(on, seen)
    on('mcp.call', () => ({ value: text(`${JSON.stringify(SEARCH)}${RECEIPT}`) }))
    const ran = await $.command.run(atlan('skills pii'))
    expect(ran.text).toBe('Pick a skill in the Atlan skills pane.')
    expect(seen.opened).toEqual(['atlan-skills'])

    const ui = await $.ui.mount(pane('atlan-skills'))
    expect((await ui.find({ type: 'Text', text: /found for/ }))?.text).toBe('7 found for "pii". Pick one to put it in your prompt.')
    await ui.press({ key: 'skill-skill_01a' })
    expect(seen.filled).toEqual(['/atlanai:use-skill skill_01a '])
    expect(seen.closed).toEqual(['atlan-skills'])
    await ui.unmount()
  })

  test('searches again from the field', async ($, on) => {
    const seen = fresh()
    engine(on, seen)
    const queries: unknown[] = []
    on('mcp.call', (_, e) => {
      queries.push(((e.args as { arguments?: { query?: unknown } }).arguments ?? {}).query)
      return { value: text(`${JSON.stringify(SEARCH)}${RECEIPT}`) }
    })
    await $.command.run(atlan('skills'))
    const ui = await $.ui.mount(pane('atlan-skills'))
    expect((await ui.find({ type: 'Text', text: /Search your organization/ }))?.text).toBe('Search your organization’s skills by what they do.')
    await ui.input({ key: 'skill-query', text: 'masking' })
    expect(queries).toEqual(['masking'])
    expect((await ui.find({ type: 'Text', text: /found for/ }))?.text).toBe('7 found for "masking". Pick one to put it in your prompt.')
    await ui.unmount()
  })
})

describe('registry text from other people', () => {
  test('cannot move the cursor or reorder what is drawn', () => {
    const hostile = { ...ARTIFACT, display_name: 'evil\u001b[2J‮name' }
    expect(summarizeResult(JSON.stringify(hostile), false)).toBe('skill evil [2J name 0.2.0')
    expect(describeCall(CALL, { name: 'registry__search_artifacts', arguments: { query: 'a\u0007b' } })).toBe('searched the registry for "a b"')
  })
})

describe('a folded run of Atlan calls', () => {
  const group = (calls: ToolGroupCall[]) =>
    ({
      plugin: PLUGIN,
      surface: 'terminal',
      component: 'ToolGroup',
      requestId: 'group-1',
      props: { calls, isActive: false, isExpanded: false },
    }) as const

  test('reads as one line per call, and a mixed run stays folded', async ($, on) => {
    engine(on, fresh())
    on('ui.render', ($, e) => {
      const { Text } = $.ui.resolve(e)
      return <Text>engine</Text>
    })
    const search = {
      tool_use_id: 'toolu_1',
      tool: CALL,
      input: { name: 'registry__search_artifacts', arguments: { query: 'pii', kind: 'skill' } },
      isRunning: false,
      isErrored: false,
      isInterrupted: false,
      output: [{ type: 'text', text: `${JSON.stringify(SEARCH)}${RECEIPT}` }],
    }
    // A model may send `arguments` as a JSON string; it reads the same.
    const read = { ...search, tool_use_id: 'toolu_2', input: { name: 'registry__get_artifact', arguments: '{"artifact_id":"skill_01target"}' }, isRunning: true, output: undefined }
    // The model loading the Atlan tools first does not keep the run folded.
    const loaded = { ...search, tool_use_id: 'toolu_0', tool: 'ToolSearch', input: { query: 'select:mcp__atlan__mcp__call_tool' } }
    const ui = await $.ui.mount(group([loaded, search, read]))
    const lines = (await ui.findAll({ type: 'Text' })).map(found => found.text)
    expect(lines.some(line => line.includes('ToolSearch') || line.includes('select:'))).toBe(false)
    expect(lines).toContain('Atlan · searched skills for "pii"')
    expect(lines).toContain('Atlan · read skill_01target …')
    await ui.unmount()

    const mixed = await $.ui.mount(group([search, { ...search, tool: 'Read', input: { file_path: '/x' } }]))
    expect((await mixed.find({ type: 'Text' }))?.text).toBe('engine')
    await mixed.unmount()
  })

  test('a published skill carries its released version into the pane', () => {
    const slot = { skills: [{ status_code: 201, id: 'skill_01new', name: 'deploy-preview', artifact_status: 'active', current_releases: [{ version_label: '0.1.0' }] }] }
    expect(touchesOf('extension__skill__create', PUBLISH_ARGS, JSON.stringify(slot), false)[0]?.version).toBe('0.1.0')
  })
})
