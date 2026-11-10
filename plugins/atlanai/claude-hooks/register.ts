// The atlanai publish helper, for Claude Code only. It lets `extension__skill__create` take
// `files_from`, a folder in the working tree, in place of the skill's files: the hook reads that
// folder, checks it with the rules of skills/atlan-author-skills/scripts/check_bundle.py, and sends
// its files inline in the same call, each with its sha256. Bulk creates can load one digest-bound
// items_from JSON file instead of model-copied definitions, under the same read and routing limits.
//
// Beside it, notices.ts adds two one-line notes: that a newer release is out (from one read of the
// public manifest on main, LATEST_URL), and an offer to draft feedback for Atlan after a failed
// Atlan call or, one turn in fifty, after Atlan work. Feedback is never sent by a hook.
//
// Every hook here fails open: a bug passes the call on as the model wrote it, and the skill app
// refuses `files_from` by name, saying to send `files`. So a fault costs speed, never a publish.
//
// The helper says it is on beside a prompt once a gateway runner is available (prompt.submit's
// `context`), not in the system prompt. Claude Code's built-in cc-plugin-sec-default, seated
// outermost for Team and Enterprise organizations and on machines with managed settings, passes
// every installed plugin over on prompt.compose and prompt.context, and tells the session the hook
// was skipped; it leaves prompt.submit and tool.call to them, so the expansion below still runs.

import type { EngineInterface, Register } from 'claude-code'

import { CREDENTIAL_DIRS, KEY_FILES, MAX_DEPTH, MAX_FILES, MAX_BYTES, check, fromBase64, isSkipped, render, type Entry, type Problem } from './bundle'
import {
  ATLAN_TOOL,
  FEEDBACK_ON_FAILURE,
  FEEDBACK_SAMPLED,
  FEEDBACK_SAMPLE_RATE,
  UPDATE_WAIT_MS,
  isExpected,
  isFlagOn,
  isNewer,
  marketplaceOf,
  updateNotice,
  versionOf,
} from './notices'
import { LATEST_URL, VERSION } from './release'

/**
 * The gateway's `call_tool` or `call_write_tool` (a search answer can name either as `run_with`),
 * under the plugin's server or an alias, narrowed by gatewayRunners before any local expansion.
 */
// The host chooses the MCP connection and retains its normal permission checks. Recognize
// the gateway's runner independently of the user-configured server key.
export const CALL_TOOL = /^mcp__[A-Za-z0-9_-]+__mcp__call(?:_(?:write|read))?_tool$/
const PLUGIN_CALL_TOOL = /^mcp__(?:plugin_[A-Za-z0-9_-]+_)?atlan__mcp__call(?:_(?:write|read))?_tool$/
const ATLAN_ALIAS_CALL_TOOL = /^mcp__(?:plugin_[A-Za-z0-9_-]+_)?atlan(?:[-_][A-Za-z0-9_-]+)?__mcp__call(?:_(?:write|read))?_tool$/

/** Aliases must also expose the gateway's discovery tools, not just a similarly named runner. */
export function gatewayRunners(tools: readonly { name: string; mcp: boolean }[]): string[] {
  const names = new Set(tools.filter(tool => tool.mcp).map(tool => tool.name))
  return [...names].filter(name => {
    if (!CALL_TOOL.test(name)) return false
    if (PLUGIN_CALL_TOOL.test(name)) return true
    // Tool names alone cannot establish an arbitrary server's origin. Without endpoint
    // metadata, ambiguous names retain the native fallback and never load local bytes.
    if (!ATLAN_ALIAS_CALL_TOOL.test(name)) return false
    const prefix = name.slice(0, name.lastIndexOf('__mcp__'))
    return names.has(`${prefix}__mcp__search_tools`) && names.has(`${prefix}__mcp__describe_tools`)
  })
}
export const PUBLISH = 'extension__skill__create'
export const BULK_PUBLISH = 'registry__create_artifacts'
export const BULK_CAPABILITY = 'Atlan bulk publish helper is on: registry__create_artifacts accepts items_from with items: [].'
export const RECEIPT_CAPABILITY = 'Atlan receipt helper is on: publication calls accept receipt_dir; released readbacks are saved without model copying.'
const RECEIPT_OPERATIONS = new Set([BULK_PUBLISH, 'registry__get_artifact', 'registry__get_related_artifacts'])
/** The line that tells the model the helper is on, once a conversation. publish.md sends `files_from` without waiting for it. */
export const CAPABILITY = 'Atlan publish helper is on: extension__skill__create accepts files_from.'

/** Which atlanai skill fits which ask, for hosts that drop skill descriptions from a crowded listing. */
export const ROUTING =
  'Atlan skills by ask: find, browse or count what exists, or recent runs: atlan-find-artifacts; use, install or ' +
  'update a registry skill: atlan-use-skill; publish a skill: atlan-author-skills; an agent: atlan-author-agents; ' +
  'a glossary, runbook or other knowledge: atlan-author-knowledge; share, save or record a report or other output: ' +
  'atlan-share; read or upload a raw file: atlan-content; archive, move, deprecate, release or approve: ' +
  'atlan-manage-artifacts; connect a GitHub repo or CI: atlan-connect-repo; another product through Atlan: ' +
  'atlan-use-mcp-servers; resume a handed-over handle: atlan-continue; an Atlan error: atlan-troubleshoot.'

const NOT_SENT = 'Nothing was sent. Fix what is listed and call extension__skill__create again.'

type Dollar = EngineInterface
type Folder = { label: string; entries: Entry[]; problems: Problem[] }

/** The same bounded, atomic no-follow writer used by Codex and Cursor command hooks. */
async function receiptProcess($: Dollar, runtime: { python: string }, mode: 'check' | 'capture', directory: unknown, request: Record<string, unknown>, response?: { text: string; isError?: boolean }) {
  const payload = { cwd: await $.session.cwd(), roots: await roots($), receipt_dir: directory, request,
    ...(response ? { response_text: response.text, is_error: response.isError === true } : {}) }
  const ran = await $.process.run([runtime.python, `${$.plugin.root}/command-hooks/receipt_store.py`, mode], {
    stdin: JSON.stringify(payload), timeoutMs: 5000,
  })
  if (ran.exitCode !== 0) throw new Error('Receipt recording is pending.')
  const { python_executable: python, ...result } = JSON.parse(ran.stdout) as Record<string, unknown>
  // Avoid repeatedly running an environment manager's launcher; every validation still runs.
  if (typeof python === 'string' && python.startsWith('/') && !python.includes('\0')) runtime.python = python
  return result
}

/** Load one reviewed batch. The remote fallback has an empty items list and cannot write. */
export async function expandBulk($: Dollar, args: Record<string, unknown>): Promise<{ args: Record<string, unknown> } | { deny: string } | undefined> {
  if (!('items_from' in args)) return undefined
  const deny = { deny: 'The local publication batch was refused. Check its path, digest, workspace, size and content locally. Nothing was sent.' }
  try {
    if (!Array.isArray(args.items) || args.items.length !== 0 || Object.keys(args).sort().join(',') !== 'items,items_from,items_sha256,kind,workspace_id') return deny
    if (!['metric', 'sql_query'].includes(String(args.kind)) || typeof args.workspace_id !== 'string' || !args.workspace_id) return deny
    const label = args.items_from
    if (typeof label !== 'string' || !label.endsWith('.json') || label.startsWith('~') || label.startsWith('//') || label.startsWith('\\\\') || /^[A-Za-z]:(?![\\/])/.test(label) || label.split(/[\\/]/).includes('..')) return deny
    if (typeof args.items_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(args.items_sha256)) return deny
    const cwd = await $.session.cwd()
    const sep = separatorOf(cwd)
    const path = isAbsolute(label) ? label : `${trimEnd(cwd)}${sep}${label}`
    const trees = await roots($)
    const tree = trees.find(root => isUnder(path, root, sep))
    if (!tree) return deny
    let at = tree
    const parts = path.slice(tree.length).split(/[\\/]/).filter(Boolean)
    if (parts.length < 1 || parts.length > MAX_DEPTH) return deny
    for (const [index, part] of parts.entries()) {
      if (isCredentialDir(part) || KEY_FILES.test(part.toLowerCase())) return deny
      at += sep + part
      const own = await $.fs.stat(at)
      if (own.isLink || own.kind !== (index === parts.length - 1 ? 'file' : 'dir')) return deny
    }
    const stat = await $.fs.stat(path, { resolve: true })
    if (stat.isLink || stat.kind !== 'file' || !stat.realPath || !isUnder(stat.realPath, tree, sep) || stat.size > MAX_BYTES) return deny
    const bytes = fromBase64((await $.fs.read(path, { as: 'bytes' })).base64)
    if (bytes.length > MAX_BYTES) return deny
    const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))
    if (Array.from(hash, b => b.toString(16).padStart(2, '0')).join('') !== args.items_sha256) return deny
    const checked = check([{ label: 'payload', entries: [{ path: parts[parts.length - 1] as string, kind: 'file', bytes }] }], false)
    if (!checked.ok) return deny
    const items: unknown = JSON.parse(Object.values(checked.files)[0] as string)
    if (!Array.isArray(items) || items.length < 1 || items.length > 100) return deny
    const ids = new Set<string>()
    for (const item of items) {
      if (!isRecord(item) || item.workspace_id !== args.workspace_id || !isRecord(item.definition) || typeof item.name !== 'string' || !item.name || typeof item.source_id !== 'string' || !item.source_id || ids.has(item.source_id)) return deny
      ids.add(item.source_id)
    }
    // Scan decoded strings too, so JSON escapes cannot hide credentials or invisible text.
    const strings = (value: unknown): string[] => typeof value === 'string' ? [value] : Array.isArray(value) ? value.flatMap(strings) : isRecord(value) ? Object.entries(value).flatMap(([key, part]) => [key, ...strings(part)]) : []
    for (const text of [JSON.stringify(items), strings(items).join('\n')]) {
      if (!check([{ label: 'payload', entries: [{ path: 'items.txt', kind: 'file', bytes: new TextEncoder().encode(text) }] }], false).ok) return deny
    }
    const expanded = { kind: args.kind, items }
    if (new TextEncoder().encode(JSON.stringify(expanded)).length > MAX_BYTES) return deny
    return { args: expanded }
  } catch {
    return deny
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)

/** `arguments` as the model sent it: an object, or the same as a JSON string. */
function argsOf(value: unknown): Record<string, unknown> | undefined {
  if (isRecord(value)) return value
  if (typeof value !== 'string') return undefined
  try {
    const parsed: unknown = JSON.parse(value)
    return isRecord(parsed) ? parsed : undefined
  } catch {
    return undefined
  }
}

const problem = (path: string, rule: string, fix: string): Problem => ({ path, line: null, column: null, rule, fix })

const separatorOf = (path: string) => (path.includes('\\') && !path.includes('/') ? '\\' : '/')
const isAbsolute = (path: string) => path.startsWith('/') || /^[A-Za-z]:[\\/]/.test(path)
const trimEnd = (path: string) => (path.length > 1 ? path.replace(/[\\/]+$/, '') : path)

/** Case folded: `.SSH` is `.ssh` on the file systems macOS and Windows use by default. */
const isCredentialDir = (name: string) => CREDENTIAL_DIRS.has(name.toLowerCase())

/** Whether `real` is `root` or under it. Both come from the same resolver, so they compare as written. */
const isUnder = (real: string, root: string, sep: string) => real === root || real.startsWith(root.endsWith(sep) ? root : root + sep)

/** The working tree's roots, resolved: the session's directory and its project root. */
async function roots($: Dollar): Promise<string[]> {
  const found: string[] = []
  for (const dir of [await $.session.cwd(), await $.session.root()]) {
    const stat = await $.fs.stat(dir, { resolve: true }).catch(() => undefined)
    if (stat?.kind === 'dir' && stat.realPath && !found.includes(stat.realPath)) found.push(trimEnd(stat.realPath))
  }
  return found
}

/**
 * Where `files_from` lands, or the problem that refuses it: a folder, not itself a link, whose real
 * path is inside the working tree and passes through no credential folder.
 */
async function place($: Dollar, label: string, cwd: string, trees: readonly string[]): Promise<{ real: string; sep: string } | Problem> {
  const outside = problem(label, 'files-from', 'must name a skill folder inside the working tree; copy it in, or send `files`')
  if (label.trim() === '' || label.startsWith('~') || /^[\\/][\\/]/.test(label) || /^[A-Za-z]:(?![\\/])/.test(label)) return outside
  const sep = separatorOf(cwd)
  const path = isAbsolute(label) ? label : `${trimEnd(cwd)}${sep}${label}`
  const own = await $.fs.stat(path).catch(() => undefined)
  if (!own) return problem(label, 'files-from', 'is not there; name the skill folder, relative to the working directory')
  if (own.isLink) return problem(label, 'symbolic-link', 'is a symbolic link; name the folder it points to')
  if (own.kind !== 'dir') return problem(label, 'files-from', 'is not a folder; name the folder that holds SKILL.md')
  const real = (await $.fs.stat(path, { resolve: true }).catch(() => undefined))?.realPath
  if (!real) return outside
  const tree = trees.find(root => isUnder(real, root, sep))
  if (!tree) return outside
  const inside = real.slice(tree.length).split(/[\\/]/).filter(Boolean)
  if (inside.some(isCredentialDir)) return problem(label, 'credential-folder', 'is inside a credential folder; it is never sent')
  return { real: trimEnd(real), sep }
}

/**
 * Walk the placed folder and read its files, stopping as soon as a limit is passed: the listing
 * sizes say so before anything is read. Links, credential folders and special files are named as
 * problems and never followed or read.
 */
async function walk($: Dollar, label: string, base: string, sep: string): Promise<Folder> {
  const problems: Problem[] = []
  const found: { rel: string; size: number }[] = []
  const links: Entry[] = []
  let total = 0
  const pending: string[] = ['']
  while (pending.length > 0) {
    const rel = pending.shift() as string
    const dir = rel === '' ? base : `${base}${sep}${rel.split('/').join(sep)}`
    const listed = [...(await $.fs.list(dir))].sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
    for (const entry of listed) {
      const path = rel === '' ? entry.name : `${rel}/${entry.name}`
      if (entry.isLink) {
        links.push({ path, kind: 'link' })
      } else if (entry.kind === 'dir') {
        if (isSkipped(entry.name, true)) continue
        if (isCredentialDir(entry.name)) problems.push(problem(path, 'credential-folder', 'is a credential folder; move it out of the skill'))
        else if (path.split('/').length >= MAX_DEPTH) problems.push(problem(path, 'unsafe-path', `holds files deeper than ${MAX_DEPTH} levels; flatten it`))
        else pending.push(path)
      } else if (entry.kind === 'file') {
        if (isSkipped(entry.name, false)) continue
        found.push({ rel: path, size: entry.size })
        total += entry.size
      } else {
        problems.push(problem(path, 'not-a-file', 'is not a regular file; leave it out'))
      }
      if (found.length > MAX_FILES) {
        problems.push(problem(label, 'too-many-files', `holds over ${MAX_FILES} files; inline \`files\` take at most ${MAX_FILES} a call, so use the ZIP route`))
        return { label, entries: links, problems }
      }
      if (total > MAX_BYTES) {
        problems.push(problem(label, 'too-large', `holds over ${MAX_BYTES} bytes; inline \`files\` take at most ${MAX_BYTES} a call, so use the ZIP route`))
        return { label, entries: links, problems }
      }
    }
  }
  const entries: Entry[] = [...links]
  for (const file of found) {
    const path = `${base}${sep}${file.rel.split('/').join(sep)}`
    // Read only what is still a plain file inside the folder: a file swapped for a link since the
    // listing is refused, not followed.
    const stat = await $.fs.stat(path, { resolve: true })
    if (stat.isLink || stat.kind !== 'file' || !stat.realPath || !isUnder(stat.realPath, base, sep)) {
      entries.push({ path: file.rel, kind: 'link' })
      continue
    }
    const read = await $.fs.read(path, { as: 'bytes' })
    entries.push({ path: file.rel, kind: 'file', bytes: fromBase64(read.base64) })
  }
  return { label, entries, problems }
}

/**
 * One budget for the call: the expanded folders' files and the other skills' inline `files`
 * together, as the skill app counts them.
 */
export function overBudget(skills: readonly unknown[], slots: readonly number[], checked: { count: number; bytes: number }): Problem[] {
  let count = 0
  let bytes = 0
  const encoder = new TextEncoder()
  for (const [index, skill] of skills.entries()) {
    if (slots.includes(index) || !isRecord(skill) || !isRecord(skill.files)) continue
    for (const text of Object.values(skill.files)) {
      count += 1
      if (typeof text === 'string') bytes += encoder.encode(text).length
    }
  }
  if (count === 0) return []
  count += checked.count
  bytes += checked.bytes
  if (count > MAX_FILES)
    return [problem('', 'too-many-files', `the call holds ${count} files with its inline \`files\`; a call takes at most ${MAX_FILES}, so split it or use the ZIP route`)]
  if (bytes > MAX_BYTES)
    return [problem('', 'too-large', `the call holds ${bytes} bytes with its inline \`files\`; a call takes at most ${MAX_BYTES}, so split it or use the ZIP route`)]
  return []
}

/**
 * The create's arguments with every `files_from` replaced by the folder's `files`, or the text that
 * refuses the call, or undefined when no skill names `files_from`.
 */
export async function expand($: Dollar, args: Record<string, unknown>): Promise<{ args: Record<string, unknown> } | { deny: string } | undefined> {
  const skills = Array.isArray(args.skills) ? args.skills : undefined
  if (!skills || !skills.some(skill => isRecord(skill) && 'files_from' in skill)) return undefined
  const cwd = await $.session.cwd()
  const trees = await roots($)
  const folders: Folder[] = []
  const slots: number[] = []
  for (const [index, skill] of skills.entries()) {
    if (!isRecord(skill) || !('files_from' in skill)) continue
    const label = typeof skill.files_from === 'string' ? skill.files_from : ''
    if (typeof skill.files_from !== 'string') {
      folders.push({ label: `skills[${index}]`, entries: [], problems: [problem(`skills[${index}]`, 'files-from', '`files_from` must be a folder path')] })
    } else if (skill.files !== undefined) {
      folders.push({ label, entries: [], problems: [problem(label, 'files-from', 'send `files` or `files_from` for a skill, never both')] })
    } else {
      const placed = await place($, label, cwd, trees)
      folders.push('real' in placed ? await walk($, label, placed.real, placed.sep) : { label, entries: [], problems: [placed] })
    }
    slots.push(index)
  }
  // Paths read as check() writes them: relative for one folder, under its label for several.
  const walked = folders.flatMap(folder =>
    folder.problems.map(one => (folders.length === 1 || one.path === folder.label ? one : { ...one, path: `${folder.label}/${one.path}` })),
  )
  // A folder that could not be placed has nothing to check, and would only add a missing SKILL.md.
  const checked = check(folders.filter(folder => folder.problems.length === 0 || folder.entries.length > 0))
  const problems = [...walked, ...checked.problems, ...overBudget(skills, slots, checked)]
  if (problems.length > 0) return { deny: `${render({ ...checked, ok: false, problems })}\n${NOT_SENT}` }
  const out = [...skills]
  for (const [k, index] of slots.entries()) {
    const { files_from: _, sha256: __, ...rest } = out[index] as Record<string, unknown>
    const files = checked.byFolder[k]
    out[index] = { ...rest, files, sha256: await digests(files) }
  }
  return { args: { ...args, skills: out } }
}

/**
 * The lowercase hex sha256 of each file's UTF-8 text as sent, so the skill app (4.2.0 on) refuses
 * the call if any of it changed on the way. It replaces any `sha256` the call carried: the hook
 * made the files.
 */
export async function digests(files: Record<string, string>): Promise<Record<string, string>> {
  const encoder = new TextEncoder()
  const out: Record<string, string> = {}
  for (const [path, text] of Object.entries(files)) {
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', encoder.encode(text)))
    out[path] = Array.from(digest, byte => byte.toString(16).padStart(2, '0')).join('')
  }
  return out
}

/** Whether a failed create may have committed: the reply was lost, not refused. */
export function isLostReply(text: string): boolean {
  if (/"may_have_run"\s*:\s*true/.test(text)) return true
  if (/"retryable"\s*:\s*false/.test(text)) return false
  return /"retryable"\s*:\s*true/.test(text) || /\b(?:502|503|504)\b|bad gateway|service unavailable|gateway time-?out/i.test(text)
}

/** Whether every skill in the create carries an idempotency_key, so resending it unchanged is safe. */
export function isKeyed(args: Record<string, unknown>): boolean {
  const skills = args.skills
  return (
    Array.isArray(skills) &&
    skills.length > 0 &&
    skills.every(item => isRecord(item) && typeof item.idempotency_key === 'string' && item.idempotency_key !== '')
  )
}

/** What the model reads after a lost reply: a keyed resend, or publish.md's read-back with the time the call went. */
export function readBackNote(sentAt: number, withKey = false): string {
  if (withKey) {
    return (
      'The skill create may have committed before its reply was lost. Every skill carried an idempotency_key, so send the ' +
      'same call again, unchanged: the same keys and the same files_from or files. The server returns the version the first ' +
      'call saved, or creates it if nothing landed. A new key, or changed files under the same key, would write a second ' +
      'version or be refused.'
    )
  }
  return (
    `The skill create may have committed before its reply was lost. It carried no idempotency_key, so do not send it again yet. ` +
    `It was sent at ${new Date(sentAt).toISOString()}: find the skill with registry__search_artifacts (kind "skill", the workspace_id, name ` +
    'and include_unreviewed: true, since a version still under scan is left out otherwise), ' +
    'read it with registry__get_artifact, and count the version as landed when its version_ordinal is the one expected and its updated_at ' +
    'is no earlier than 10 seconds before that time. If nothing landed, wait 5 seconds and read once more; only then send the create again, once.'
  )
}

function resultText(ran: { text?: unknown; result?: unknown }): string {
  if (typeof ran.text === 'string') return ran.text
  const result = ran.result
  if (typeof result === 'string') return result
  const blocks = isRecord(result) && Array.isArray(result.content) ? result.content : Array.isArray(result) ? result : []
  return blocks.map(block => (isRecord(block) && typeof block.text === 'string' ? block.text : '')).join('\n')
}

/** Whether the user turned the feedback offers off, the way other tools' telemetry is. */
async function isFeedbackOff($: Dollar): Promise<boolean> {
  return isFlagOn(await $.env.get('DO_NOT_TRACK').catch(() => undefined)) || isFlagOn(await $.env.get('DISABLE_TELEMETRY').catch(() => undefined))
}

/** The update notice, or undefined: off when the user turned the check or nonessential traffic off. */
async function checkLatest($: Dollar): Promise<string | undefined> {
  const check = await $.env.get('ATLAN_PLUGIN_UPDATE_CHECK')
  if (check === '0' || /^false$/i.test(check ?? '')) return undefined
  if (isFlagOn(await $.env.get('CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC'))) return undefined
  const response = await $.http.fetch(LATEST_URL)
  const latest = response.ok ? versionOf(response.text) : undefined
  if (!latest || !isNewer(latest, VERSION)) return undefined
  return updateNotice($.plugin.name, VERSION, latest, marketplaceOf($.plugin.root, $.plugin.name))
}

export const register: Register = on => {
  const receiptRuntime = { python: 'python3' }
  // The conversations already told, by session id: a /clear goes on under a new id, and is told again.
  const told = new Set<string>()
  const capabilitiesTold = new Map<string, Set<string>>()
  // The update notice, read once a process and given once; the conversations offered feedback; and
  // those whose last turn called an Atlan tool, the only turns the sampled offer follows.
  let latest: Promise<string | undefined> | undefined
  let isUpdateTold = false
  const offered = new Set<string>()
  const usedAtlan = new Set<string>()

  on('session.start', async ($, e, next) => {
    latest ??= checkLatest($).catch(() => undefined)
    return next(e)
  }).catch(($, e, next) => next(e))

  on('prompt.submit', async ($, e, next) => {
    // A slash command's or a hidden prompt's context is not the conversation's own: tell the next one.
    if (e.command !== undefined || e.isMeta === true) return next(e)
    const session = await $.session.id()
    const notes: string[] = []
    // Discovery can finish after the first prompt. Announce only routes that exist now,
    // and do not suppress a later capability just because routing was already shown.
    let runners: string[] = []
    try {
      runners = gatewayRunners(await $.tool.list())
    } catch { /* An unavailable inventory is not evidence that a helper is on. */ }
    const capabilities = capabilitiesTold.get(session) ?? new Set<string>()
    const writable = runners.some(name => !name.endsWith('__mcp__call_read_tool'))
    if (writable && !capabilities.has(CAPABILITY)) notes.push(CAPABILITY)
    if (!told.has(session)) {
      told.add(session)
      notes.push(ROUTING)
    }
    if (writable && !capabilities.has(BULK_CAPABILITY)) notes.push(BULK_CAPABILITY)
    if (runners.length && !capabilities.has(RECEIPT_CAPABILITY)) notes.push(RECEIPT_CAPABILITY)
    for (const note of notes) if (note !== ROUTING) capabilities.add(note)
    capabilitiesTold.set(session, capabilities)
    // Each courtesy notice fails alone, preserving routing and available capabilities.
    if (!isUpdateTold) {
      try {
        latest ??= checkLatest($).catch(() => undefined)
        const notice = await Promise.race([latest, $.clock.sleep(UPDATE_WAIT_MS).then(() => null)])
        // null: not answered yet, so the next prompt looks again; undefined: nothing to say.
        if (notice !== null) isUpdateTold = true
        if (notice) notes.push(notice)
      } catch {
        isUpdateTold = true
      }
    }
    const hadAtlan = usedAtlan.delete(session)
    if (hadAtlan && !offered.has(session) && Math.random() < FEEDBACK_SAMPLE_RATE && !(await isFeedbackOff($))) {
      offered.add(session)
      notes.push(FEEDBACK_SAMPLED)
    }
    return notes.length > 0 ? next({ ...e, context: [...(e.context ?? []), ...notes] }) : next(e)
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: ATLAN_TOOL }, async ($, e, next) => {
    const ran = await next(e)
    // From here the call has run: nothing below may throw, or the .catch would send it again.
    try {
      const session = await $.session.id()
      usedAtlan.add(session)
      if (ran.deny !== undefined || ran.isError !== true || offered.has(session)) return ran
      if (isExpected(resultText(ran)) || (await isFeedbackOff($))) return ran
      offered.add(session)
      return { ...ran, context: [...(ran.context ?? []), FEEDBACK_ON_FAILURE] }
    } catch {
      return ran
    }
  }).catch(($, e, next) => next(e))
  on('tool.call', { tool: CALL_TOOL }, async ($, e, next) => {
    const call = e as unknown as { name?: unknown; arguments?: unknown }
    if (call.name !== PUBLISH && !RECEIPT_OPERATIONS.has(String(call.name))) return next(e)
    const args = argsOf(call.arguments)
    if (!args) return next(e)
    const { receipt_dir: receiptDir, ...native } = args
    if (!PLUGIN_CALL_TOOL.test(e.tool) && !gatewayRunners(await $.tool.list()).includes(e.tool)) return next(e)
    if (receiptDir !== undefined && (call.name === PUBLISH || call.name === BULK_PUBLISH && !('items_from' in native))) return { deny: 'Receipt capture on creates requires a guarded local batch.' }
    const expanded = (call.name === BULK_PUBLISH ? await expandBulk($, native) : call.name === PUBLISH ? await expand($, native) : undefined) ?? (receiptDir !== undefined ? { args: native } : undefined)
    if (!expanded) return next(e)
    if ('deny' in expanded) return { deny: expanded.deny }
    const request = { name: call.name, arguments: expanded.args }
    if (receiptDir !== undefined) {
      try {
        await receiptProcess($, receiptRuntime, 'check', receiptDir, request)
      } catch {
        return { deny: 'The receipt destination could not be prepared. Nothing was sent.' }
      }
    }
    const sentAt = await $.clock.now()
    const sent = typeof call.arguments === 'string' ? JSON.stringify(expanded.args) : expanded.args
    // From here the call has run: nothing below may throw, or the .catch would send it again.
    const ran = await next({ ...e, arguments: sent } as typeof e)
    try {
      if (receiptDir !== undefined) {
        try {
          const saved = await receiptProcess($, receiptRuntime, 'capture', receiptDir, request, { text: resultText(ran), isError: ran.isError })
          return { ...ran, context: [...(ran.context ?? []), `Atlan receipt saved: ${JSON.stringify(saved)}. Assemble receipts with the Analytics receipt helper; do not copy response bodies.`] }
        } catch {
          return { ...ran, context: [...(ran.context ?? []), 'Receipt recording is pending. The tool already ran. Reconcile its result; never repeat a write to repair a local receipt.'] }
        }
      }
      if (call.name === BULK_PUBLISH || ran.deny !== undefined || ran.isError !== true || !isLostReply(resultText(ran))) return ran
      return { ...ran, context: [...(ran.context ?? []), readBackNote(sentAt, isKeyed(args))] }
    } catch {
      return ran
    }
  }).catch(($, e, next) => next(e))
}
