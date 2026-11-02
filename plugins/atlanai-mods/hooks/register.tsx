// Registry Mods: a Claude Code mod for the atlanai plugin's `atlan` server.
//
// tool.check (mcp__call_tool): registry reads run without a prompt; writes ask, naming the
//   write; archive, approve and move always ask, naming what depends on the artifact, and the
//   Atlan pane shows the same as a card while the question waits.
// tool.call (the three gateway tools): moves session_id, rationale and objective out of
//   `arguments`, reuses the session's open audit session, refuses a call with no rationale,
//   learns tool annotations and the session id from results, and records what each call read,
//   changed or published for the pane and the publish band.
// ui.render: each gateway call's transcript row reads as one line of what it did and found; the
//   `atlan` pane lists the artifacts this session touched; the band above the prompt follows a
//   skill publish to its end; the `atlan-skills` pane picks a skill to load.
// session.start: checks the connection without opening an audit session, and puts a problem,
//   never a success, on the status line. /atlan reports it; /atlan search, skills and pane.
//
// What the session learned lives in this module, one copy per session. It is not kept in the
// host's plugin state, which needs a types contract named in plugin.json: a key the desktop app
// does not read and warns about on install. A hot reload forgets it, and the next registry call
// learns it again. A change redraws with `$.ui.invalidate('ui.render')`.
//
// The host reads `on(...)` and `$.noun.method(...)` from source, so both are spelled literally.

import type { EngineInterface, Register } from 'claude-code'

import {
  CALL_TOOL,
  FIX,
  GATEWAY_TOOL,
  PROBE_SERVERS,
  artifactOf,
  classify,
  dependentsOf,
  describeBlastRadius,
  describeWrite,
  diagnose,
  findSessionId,
  harvestHints,
  isStaleSession,
  repairRails,
  resultText,
  searchRows,
  summaryOf,
  type AtlanHealth,
  type CallInput,
  type HintTable,
} from './registry'
import {
  SETTLED,
  cardOf,
  clip,
  describeCall,
  followPublish,
  mergeTouches,
  settlePublishes,
  skillRows,
  startPublishes,
  summarizeResult,
  touchesOf,
  type Card,
  type Publish,
  type SkillRow,
  type Touch,
} from './view'

const HINTS_KEY = 'hints'
const LOOKUP_MS = 6000
const PROBE_MS = 15000
const RETRY_MS = 20000
const PANE = 'atlan'
const PICKER = 'atlan-skills'
const DASHBOARD = 'https://app.atlan.com'
const PUBLISH = 'extension__skill__create'

const RATIONALE_HELP =
  'Atlan needs a one-sentence `rationale` for every mcp__call_tool call, beside `name` and `arguments` (not inside `arguments`). Add it and call again.'
const GUARD_FAILED = 'The Atlan registry guard could not check this call, so it needs your confirmation.'
const USAGE =
  'Usage: /atlan (connection status), /atlan search <words>, /atlan skills [words] (pick a skill to load) or /atlan pane (what this session touched)'

type Dollar = EngineInterface
type Lookup = { text: string; isError: boolean }
type Picker = { query: string; state: 'idle' | 'searching' | 'done' | 'failed'; rows: SkillRow[]; total: number; note: string }

/** What this session has learned and shows. */
type Memory = {
  session: string
  health?: AtlanHealth
  /** Artifacts this session read or changed, newest first. */
  touches: Touch[]
  /** Confirmation cards for calls waiting on the person, by tool_use_id. */
  cards: Map<string, Card>
  /** Skill publishes this turn, for the band. */
  publishes: Publish[]
  isBandHidden: boolean
  /** The pane opens by itself once a session, at its first write or question. */
  hasOpenedPane: boolean
  picker: Picker
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** Annotations learned from the gateway, shared by every session on this machine. */
async function loadHints($: Dollar): Promise<HintTable> {
  const stored = await $.store.get(HINTS_KEY)
  return isRecord(stored) ? (stored as HintTable) : {}
}

async function saveHints($: Dollar, learned: HintTable): Promise<void> {
  if (Object.keys(learned).length === 0) return
  const current = await loadHints($)
  await $.store.set(HINTS_KEY, { ...current, ...learned })
}

function setHealth($: Dollar, mem: Memory, health: AtlanHealth): void {
  mem.health = health
  const line: Record<AtlanHealth['state'], string | undefined> = {
    ready: undefined,
    unknown: undefined,
    'sign-in': 'Atlan: sign in with /mcp',
    missing: 'Atlan: not connected, run /atlan',
    unreachable: 'Atlan: offline, run /atlan',
    error: 'Atlan: error, run /atlan',
  }
  $.ui.status(line[health.state])
}

function redraw($: Dollar): void {
  $.ui.invalidate('ui.render')
}

/** Opens the Atlan pane the first time this session writes or asks; the person may close it. */
function offerPane($: Dollar, mem: Memory, always = false): void {
  if (mem.hasOpenedPane && !always) return
  mem.hasOpenedPane = true
  void $.ui.open({ id: PANE, title: 'Atlan' }).catch(() => undefined)
}

/** A gateway call made by the mod itself, under the session's audit session when one is open. */
async function lookup($: Dollar, mem: Memory, server: string, name: string, args: Record<string, unknown>, rationale: string): Promise<Lookup> {
  const session = mem.session
  const rails = session
    ? { session_id: session }
    : { objective: 'Claude Code with the Atlan registry: the Registry Mods plugin checks registry changes and connection status for the user.' }
  const result = await $.mcp.call(server, 'mcp__call_tool', { name, arguments: args, rationale, ...rails })
  const text = result.content.map(block => (isRecord(block) && typeof block.text === 'string' ? block.text : '')).join('\n')
  const opened = findSessionId(text)
  if (opened && opened !== session) mem.session = opened
  return { text, isError: result.isError }
}

/** Settles with `undefined` once `ms` pass, so a slow registry never holds a prompt. */
async function within<T>($: Dollar, ms: number, work: Promise<T>): Promise<T | undefined> {
  const stop = new AbortController()
  const timer = $.clock.sleep(ms, { signal: stop.signal }).then(
    () => undefined,
    () => undefined,
  )
  try {
    return await Promise.race([work.catch(() => undefined), timer])
  } finally {
    stop.abort()
  }
}

/** The permission prompt's text for an archive, move or approve, and the pane's card for it. */
async function blastRadius($: Dollar, mem: Memory, callId: string, tool: string, name: string, input: Record<string, unknown>): Promise<{ reason: string; card?: Card }> {
  const id = artifactOf(input.arguments)
  if (!id) return { reason: describeWrite(name, input.arguments) }
  const server = tool.slice('mcp__'.length, tool.lastIndexOf('__mcp__'))
  const why = 'Checking what depends on this artifact before the user confirms the change.'
  const [related, artifact] = await Promise.all([
    within($, LOOKUP_MS, lookup($, mem, server, 'registry__get_related_artifacts', { artifact_id: id }, why)),
    within($, LOOKUP_MS, lookup($, mem, server, 'registry__get_artifact', { artifact_id: id }, why)),
  ])
  const summary = artifact && !artifact.isError ? summaryOf(artifact.text) : undefined
  const dependents = related && !related.isError ? dependentsOf(related.text) : undefined
  return { reason: describeBlastRadius(name, id, summary, dependents), card: cardOf(callId, name, id, summary, dependents) }
}

/** Checks the connection with `mcp__describe_tools`, which opens no audit session. */
async function probe($: Dollar): Promise<AtlanHealth> {
  let detail = 'no Atlan server answered'
  let state: AtlanHealth['state'] = 'missing'
  for (const server of PROBE_SERVERS) {
    try {
      const result = await within($, PROBE_MS, $.mcp.call(server, 'mcp__describe_tools', { servers: ['registry'], detail: 'summary' }))
      if (result === undefined) {
        state = 'unreachable'
        detail = `${server} did not answer within ${PROBE_MS / 1000} seconds`
        continue
      }
      const text = result.content.map(block => (isRecord(block) && typeof block.text === 'string' ? block.text : '')).join('\n')
      if (result.isError) {
        state = diagnose(text)
        detail = `${server} refused the check`
        continue
      }
      const learned = harvestHints(text)
      if (Object.keys(learned).length === 0) {
        state = 'error'
        detail = `${server} answered, but listed no registry tools`
        continue
      }
      await saveHints($, learned)
      return { state: 'ready', server, detail: `${Object.keys(learned).length} registry tools`, checkedAt: await $.clock.now() }
    } catch (error) {
      const found = diagnose(error instanceof Error ? error.message : String(error))
      // A server that is missing under one name may be there under the next: keep the worse state.
      if (state === 'missing' || found !== 'missing') state = found
      detail = `${server}: ${found}`
    }
  }
  return { state, server: '', detail, checkedAt: await $.clock.now() }
}

function report(mem: Memory, health: AtlanHealth, confirmWrites: boolean): string {
  const session = mem.session
  const lines = [
    health.state === 'ready' ? `Atlan: connected through ${health.server} (${health.detail}).` : `Atlan: ${health.state} (${health.detail}).`,
    FIX[health.state] ?? FIX.error,
  ]
  if (health.state === 'ready') {
    lines.push(
      confirmWrites
        ? 'Writes ask first. Archive, approve and move always ask, with what depends on the artifact.'
        : 'Writes follow your permission rules. Archive, approve and move always ask.',
    )
    lines.push(session ? 'An audit session is open and is reused for every call.' : 'No audit session yet: the first registry call opens one.')
  }
  return lines.join('\n')
}

/** Runs a registry search through the server that answered last, or each one in turn. */
async function searchRegistry($: Dollar, mem: Memory, args: Record<string, unknown>, rationale: string): Promise<Lookup | undefined> {
  const health = mem.health
  const servers = health?.state === 'ready' && health.server ? [health.server] : [...PROBE_SERVERS]
  for (const server of servers) {
    try {
      const found = await within($, PROBE_MS, lookup($, mem, server, 'registry__search_artifacts', args, rationale))
      if (found) return found
    } catch {
      // try the next server name
    }
  }
  return undefined
}

async function search($: Dollar, mem: Memory, words: string): Promise<string> {
  if (!words) return USAGE
  const found = await searchRegistry($, mem, { query: words, semantic: true, limit: 10 }, 'The user searched the registry with the /atlan search command.')
  if (!found) return `Atlan did not answer. ${FIX.unreachable}`
  if (found.isError) return `Atlan refused the search. ${FIX[diagnose(found.text)] ?? FIX.error}`
  const rows = searchRows(found.text)
  if (!rows || rows.rows.length === 0) return `Nothing in Atlan matches "${words}".`
  const more = rows.total > rows.rows.length ? `, showing ${rows.rows.length}` : ''
  return [`${rows.total} found for "${words}"${more}:`, ...rows.rows.map(row => `  ${row}`)].join('\n')
}

/** Fills the skill picker with the skills that match `words`. */
async function pickerSearch($: Dollar, mem: Memory, words: string): Promise<void> {
  const query = words.trim()
  if (!query) {
    mem.picker = { query, state: 'idle', rows: [], total: 0, note: '' }
    redraw($)
    return
  }
  mem.picker = { query, state: 'searching', rows: [], total: 0, note: '' }
  redraw($)
  const found = await searchRegistry($, mem, { query, kind: 'skill', semantic: true, limit: 15 }, 'The user searched skills with the /atlan skills picker.')
  if (mem.picker.query !== query) return
  if (!found || found.isError) {
    const why = (found ? FIX[diagnose(found.text)] : FIX.unreachable) ?? ''
    mem.picker = { query, state: 'failed', rows: [], total: 0, note: why }
  } else {
    const rows = skillRows(found.text)
    mem.picker = { query, state: 'done', rows: rows?.rows ?? [], total: rows?.total ?? 0, note: '' }
  }
  redraw($)
}

/** Puts the chosen skill in the prompt as the atlanai plugin's command, for the person to send. */
async function pickSkill($: Dollar, row: SkillRow): Promise<void> {
  await $.prompt.fill({ text: `/atlanai:use-skill ${row.id} ` })
  await $.ui.close({ id: PICKER })
}

const MARK: Record<Publish['state'], string> = {
  sending: '…',
  scanning: '…',
  published: '✓',
  blocked: '✗',
  unchanged: '=',
  failed: '✗',
}

export const register: Register = (on, options) => {
  const confirmWrites = options.confirm_writes !== false
  const mem: Memory = {
    session: '',
    touches: [],
    cards: new Map(),
    publishes: [],
    isBandHidden: false,
    hasOpenedPane: false,
    picker: { query: '', state: 'idle', rows: [], total: 0, note: '' },
  }

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'atlan',
      description: 'Atlan connection status; /atlan search <words>, /atlan skills to pick a skill, /atlan pane for what this session touched',
      argumentHint: '[search <words> | skills [words] | pane]',
      immediate: true,
    })
    const check = async (): Promise<void> => {
      const health = await probe($)
      // MCP servers can still be connecting at start: report a problem only if it outlasts a retry.
      if (health.state === 'ready') setHealth($, mem, health)
      else
        $.clock.after(RETRY_MS, async () => {
          setHealth($, mem, await probe($))
        })
    }
    void check().catch(() => undefined)
    return next(e)
  })

  on('command.run', { command: 'atlan' }, async ($, e) => {
    const [sub = '', ...rest] = e.args.trim().split(/\s+/)
    const words = rest.join(' ').trim()
    if (sub === 'search') return { text: await search($, mem, words) }
    if (sub === 'skills') {
      const opened = await $.ui.open({ id: PICKER, title: 'Atlan skills', focus: true, closeOnEscape: true })
      void pickerSearch($, mem, words)
      return { text: opened.isPlaced ? 'Pick a skill in the Atlan skills pane.' : `The skill picker could not open here. Try /atlan search ${words || '<words>'}.` }
    }
    if (sub === 'pane') {
      mem.hasOpenedPane = true
      const opened = await $.ui.open({ id: PANE, title: 'Atlan' })
      return { text: opened.isPlaced ? 'The Atlan pane lists what this session read and changed.' : 'The Atlan pane opens once the window is wide enough.' }
    }
    if (sub !== '' && sub !== 'status' && sub !== 'doctor') return { text: USAGE }
    const health = await probe($)
    setHealth($, mem, health)
    return { text: report(mem, health, confirmWrites) }
  })

  on('prompt.submit', ($, e, next) => {
    // A new request starts with a clean band: finished publishes leave it, running ones stay.
    if (mem.publishes.length > 0 && mem.publishes.every(one => SETTLED.has(one.state))) {
      mem.publishes = []
      mem.isBandHidden = false
      redraw($)
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: GATEWAY_TOOL }, async ($, e, next) => {
    const call = e as unknown as CallInput & { tool: string; tool_use_id?: string }
    const isCallTool = CALL_TOOL.test(call.tool)
    const known = mem.session
    const { input } = repairRails(call, known, isCallTool)
    if (isCallTool && (typeof input.rationale !== 'string' || input.rationale.trim() === '')) {
      return { deny: RATIONALE_HELP }
    }
    const name = typeof input.name === 'string' ? input.name : ''

    let started: Publish[] = []
    if (isCallTool && name === PUBLISH) {
      started = startPublishes(input.arguments)
      const names = new Set(started.map(one => one.name))
      mem.publishes = [...mem.publishes.filter(one => !names.has(one.name)), ...started]
      mem.isBandHidden = false
      redraw($)
    }

    let ran: Awaited<ReturnType<typeof next>>
    try {
      ran = await next({ ...e, ...input } as typeof e)
    } finally {
      // The question, if one was put, has been answered either way.
      if (call.tool_use_id && mem.cards.delete(call.tool_use_id)) redraw($)
    }
    if (ran.deny !== undefined) {
      if (started.length > 0) {
        mem.publishes = mem.publishes.map(one => (started.some(s => s.name === one.name) ? { ...one, state: 'failed' as const, text: `${one.name}: not sent` } : one))
        redraw($)
      }
      return ran
    }

    const text = resultText(ran)
    const isError = ran.isError === true
    const opened = findSessionId(text)
    if (opened && opened !== known) mem.session = opened
    else if (isError && known && input.session_id === known && isStaleSession(text)) mem.session = ''

    if (!isCallTool) await saveHints($, harvestHints(text))

    if (isCallTool) {
      if (started.length > 0) {
        const settled = settlePublishes(started, text, isError)
        mem.publishes = mem.publishes.map(one => settled.find(s => s.name === one.name) ?? one)
        for (const one of settled) {
          if (one.state === 'published' || one.state === 'blocked') $.ui.toast(`Atlan: ${one.text}`)
        }
      } else if (mem.publishes.length > 0) {
        const before = mem.publishes
        mem.publishes = followPublish(before, name, text, isError)
        mem.publishes.forEach((one, i) => {
          if (one.state !== before[i]?.state && (one.state === 'published' || one.state === 'blocked')) $.ui.toast(`Atlan: ${one.text}`)
        })
      }
      const touched = touchesOf(name, input.arguments, text, isError)
      if (touched.length > 0) {
        mem.touches = mergeTouches(mem.touches, touched)
        if (touched.some(one => one.action !== 'read')) offerPane($, mem)
      }
      redraw($)
    }

    const health = mem.health
    if (!isError && health?.state !== 'ready') {
      setHealth($, mem, { state: 'ready', server: health?.server ?? '', detail: 'a registry call succeeded', checkedAt: await $.clock.now() })
    } else if (isError && diagnose(text) === 'sign-in') {
      setHealth($, mem, { state: 'sign-in', server: health?.server ?? '', detail: 'a registry call was refused', checkedAt: await $.clock.now() })
    }
    return ran
  }).catch(($, e, next) => next(e))

  on('tool.check', { tool: CALL_TOOL }, async ($, e, next) => {
    const verdict = await next(e)
    if (verdict.decision === 'deny') return verdict

    const input = isRecord(e.input) ? e.input : {}
    const name = typeof input.name === 'string' ? input.name : ''
    const effect = classify(name, await loadHints($))

    if (effect === 'unknown') return verdict
    if (effect === 'read') {
      // An organization's ceiling on the connector stands above this mod.
      if (e.ceiling === 'ask' || e.ceiling === 'deny') return verdict
      return { decision: 'allow', reason: `Atlan: ${name} only reads the registry.` }
    }
    if (effect === 'write') {
      // Nobody to ask (claude -p, the Agent SDK): the session's own rules decide a write.
      if (!confirmWrites || e.approver === undefined) return verdict
      return { decision: 'ask', reason: describeWrite(name, input.arguments) }
    }
    // Archive, approve and move: asked wherever someone can answer, refused where nobody can.
    const { reason, card } = await blastRadius($, mem, e.tool_use_id ?? '', e.tool, name, input)
    if (card && e.tool_use_id && e.approver !== undefined) {
      mem.cards.set(e.tool_use_id, card)
      offerPane($, mem, true)
      redraw($)
    }
    return { decision: 'ask', reason }
  }).catch(() => ({ decision: 'ask', reason: GUARD_FAILED }))

  // 1. Each gateway call's row: what it did, in one line. ctrl+o (expanded) shows the engine's own.
  on('ui.render', { component: 'ToolUse' }, ($, e, next) => {
    if (e.component !== 'ToolUse' || !GATEWAY_TOOL.test(e.props.tool) || e.props.isExpanded === true) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    const status = e.props.isInterrupted ? ' (interrupted)' : e.props.isRunning ? ' …' : ''
    return (
      <Box>
        <Text bold>Atlan</Text>
        <Text>
          {' · '}
          {describeCall(e.props.tool, e.props.input)}
        </Text>
        <Text dimColor>{status}</Text>
      </Box>
    )
  })

  on('ui.render', { component: 'ToolResult' }, ($, e, next) => {
    if (e.component !== 'ToolResult' || !GATEWAY_TOOL.test(e.props.tool) || e.props.isExpanded === true) return next(e)
    const { Text } = $.ui.resolve(e)
    const summary = summarizeResult(resultText({ result: e.props.output }), e.props.isErrored)
    return <Text dimColor={!e.props.isErrored}>{`  ⎿ ${summary}`}</Text>
  })

  // 2 and 3. The Atlan pane: questions waiting on the person, then what this session touched.
  on('ui.render', { component: 'Pane', requestId: PANE }, ($, e, next) => {
    if (e.component !== 'Pane') return next(e)
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const width = Math.max(30, e.props.bodyColumns - 2)
    const cards = [...mem.cards.values()]
    return (
      <Box flexDirection="column">
        {cards.map(card => (
          <Box key={`card-${card.id}`} flexDirection="column" borderStyle="round" paddingX={1} marginBottom={1}>
            <Text bold>Needs your answer: {card.title}</Text>
            <Text dimColor>{clip(card.where, width)}</Text>
            <Text>
              {card.count < 0
                ? 'Could not read what depends on it.'
                : card.count === 0
                  ? 'Nothing in the registry depends on it.'
                  : `${card.count} ${card.count === 1 ? 'artifact depends' : 'artifacts depend'} on it:`}
            </Text>
            {card.dependents.slice(0, 8).map(one => (
              <Text>{`  ${one.kind} ${one.id} (${one.rel.replace(/_/g, ' ')})`}</Text>
            ))}
            {card.count > 8 ? <Text dimColor>{`  and ${card.count - 8} more`}</Text> : ''}
            {card.consequence ? <Text dimColor>{card.consequence}</Text> : ''}
            <Text dimColor>Answer in the permission prompt.</Text>
          </Box>
        ))}
        <Text bold>This session in Atlan</Text>
        {mem.touches.length === 0 ? <Text dimColor>Nothing yet. Artifacts Claude reads or changes in Atlan show here.</Text> : ''}
        {mem.touches.slice(0, 30).map(one => (
          <Box key={`touch-${one.id}`} flexDirection="row" justifyContent="space-between">
            <Text wrap="truncate-end">
              <Text dimColor>{`${one.action} `}</Text>
              {`${one.kind} ${one.name || one.id}${one.version ? ` ${one.version}` : ''}`}
              <Text dimColor>{one.status && one.status !== 'active' ? ` · ${one.status.replace(/_/g, ' ')}` : ''}</Text>
            </Text>
            <Button key={`copy-${one.id}`} label="Copy id" plain onPress={press => void $.ui.copy({ text: one.id, surface: press.surface })} />
          </Box>
        ))}
        <Link href={DASHBOARD} label="Open the Atlan dashboard" />
      </Box>
    )
  })

  // 4. The band above the prompt while skills publish, until the next request.
  on('ui.render', { component: 'AbovePrompt' }, ($, e, next) => {
    if (e.component !== 'AbovePrompt' || e.props.hasSurvey || mem.isBandHidden || mem.publishes.length === 0) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const isDone = mem.publishes.every(one => SETTLED.has(one.state))
    const blocked = mem.publishes.some(one => one.state === 'blocked')
    return (
      <Box flexDirection="column">
        <Box flexDirection="row" justifyContent="space-between">
          <Text bold>{isDone ? 'Atlan publish finished' : 'Publishing to Atlan'}</Text>
          <Button key="hide-publish" label="Hide" plain onPress={() => {
            mem.isBandHidden = true
            redraw($)
          }} />
        </Box>
        {mem.publishes.map(one => (
          <Text dimColor={one.state === 'unchanged'}>{`${MARK[one.state]} ${one.text}`}</Text>
        ))}
        {blocked ? <Text dimColor>Ask Claude to show the scan's findings for a blocked skill.</Text> : ''}
      </Box>
    )
  })

  // 5. The skill picker: search the organization's skills, pick one to load.
  on('ui.render', { component: 'Pane', requestId: PICKER }, ($, e, next) => {
    if (e.component !== 'Pane') return next(e)
    const elements = $.ui.resolve(e)
    const { Box, Text, Button } = elements
    const picker = mem.picker
    const room = Math.max(3, (e.props.scroll?.bodyRows ?? 20) - 4)
    const status =
      picker.state === 'searching'
        ? `Searching for "${picker.query}"…`
        : picker.state === 'failed'
          ? `The search failed. ${picker.note}`
          : picker.state === 'done'
            ? picker.rows.length === 0
              ? `No skill matches "${picker.query}".`
              : `${picker.total} found for "${picker.query}". Pick one to put it in your prompt.`
            : 'Search your organization’s skills by what they do.'
    return (
      <Box flexDirection="column">
        {'Input' in elements ? (
          <elements.Input
            key="skill-query"
            placeholder="What should the skill do?"
            value={picker.query}
            submitLabel="Search"
            autoFocus
            onSubmit={value => void pickerSearch($, mem, value)}
          />
        ) : (
          <Text dimColor>Run /atlan skills followed by words to search.</Text>
        )}
        <Text dimColor>{status}</Text>
        {picker.rows.slice(0, room).map(row => (
          <Button key={`skill-${row.id}`} plain onPress={() => void pickSkill($, row)}>
            {row.name}
            <Text dimColor>{`${row.version ? ` ${row.version}` : ''}${row.description ? ` · ${clip(row.description, 70)}` : ''}`}</Text>
          </Button>
        ))}
      </Box>
    )
  })
}
