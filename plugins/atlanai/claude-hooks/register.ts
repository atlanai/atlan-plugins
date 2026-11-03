// The atlanai publish helper, for Claude Code only. It lets `extension__skill__create` take
// `files_from`, a folder in the working tree, in place of the skill's files: the hook reads that
// folder, checks it with the rules of skills/atlan-author-skills/scripts/check_bundle.py, and sends
// its files inline in the same call, each with its sha256. It reads nothing else and sends nowhere else.
//
// Every hook here fails open: a bug passes the call on as the model wrote it, and the skill app
// refuses `files_from` by name, saying to send `files`. So a fault costs speed, never a publish.
//
// The helper says it is on with one line beside the conversation's first prompt (prompt.submit's
// `context`), not in the system prompt. Claude Code's built-in cc-plugin-sec-default, seated
// outermost for Team and Enterprise organizations and on machines with managed settings, passes
// every installed plugin over on prompt.compose and prompt.context, and tells the session the hook
// was skipped; it leaves prompt.submit and tool.call to them, so the expansion below still runs.

import type { EngineInterface, Register } from 'claude-code'

import { CREDENTIAL_DIRS, MAX_DEPTH, MAX_FILES, MAX_BYTES, check, fromBase64, isSkipped, render, type Entry, type Problem } from './bundle'

/**
 * The gateway's `call_tool` or `call_write_tool` (a search answer can name either as `run_with`),
 * under the plugin's server or one added by hand. Never another server's tool of a like name.
 */
export const CALL_TOOL = /^mcp__(?:plugin_[A-Za-z0-9_-]+_)?atlan__mcp__call(?:_write)?_tool$/
export const PUBLISH = 'extension__skill__create'
/** The line that tells the model the helper is on, once a conversation. publish.md sends `files_from` without waiting for it. */
export const CAPABILITY = 'Atlan publish helper is on: extension__skill__create accepts files_from.'

const NOT_SENT = 'Nothing was sent. Fix what is listed and call extension__skill__create again.'

type Dollar = EngineInterface
type Folder = { label: string; entries: Entry[]; problems: Problem[] }

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

export const register: Register = on => {
  // The conversations already told, by session id: a /clear goes on under a new id, and is told again.
  const told = new Set<string>()

  on('prompt.submit', async ($, e, next) => {
    // A slash command's or a hidden prompt's context is not the conversation's own: tell the next one.
    if (e.command !== undefined || e.isMeta === true) return next(e)
    const session = await $.session.id()
    if (told.has(session)) return next(e)
    told.add(session)
    return next({ ...e, context: [...(e.context ?? []), CAPABILITY] })
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: CALL_TOOL }, async ($, e, next) => {
    const call = e as unknown as { name?: unknown; arguments?: unknown }
    if (call.name !== PUBLISH) return next(e)
    const args = argsOf(call.arguments)
    if (!args) return next(e)
    const expanded = await expand($, args)
    if (!expanded) return next(e)
    if ('deny' in expanded) return { deny: expanded.deny }
    const sentAt = await $.clock.now()
    const sent = typeof call.arguments === 'string' ? JSON.stringify(expanded.args) : expanded.args
    // From here the call has run: nothing below may throw, or the .catch would send it again.
    const ran = await next({ ...e, arguments: sent } as typeof e)
    try {
      if (ran.deny !== undefined || ran.isError !== true || !isLostReply(resultText(ran))) return ran
      return { ...ran, context: [...(ran.context ?? []), readBackNote(sentAt, isKeyed(args))] }
    } catch {
      return ran
    }
  }).catch(($, e, next) => next(e))
}
