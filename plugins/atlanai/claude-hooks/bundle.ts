// What the publish helper checks before it sends a folder as inline `files`: the rules of
// skills/atlan-author-skills/scripts/check_bundle.py, as plain functions, so a folder the script
// passes is one the helper sends and a folder it refuses is refused here with the same rule, file,
// line and column. Both read tools/tests/fixtures/publish-bundles.json in their tests.
//
// Nothing here touches the file system: register.ts walks the folder and hands each entry in.

export const MAX_FILES = 128
export const MAX_BYTES = 256 * 1024
export const MAX_DEPTH = 8
export const SKIPPED_DIRS = new Set(['.git', '__pycache__', 'node_modules'])
export const SKIPPED_FILES = new Set(['.DS_Store'])
/** Folders `files_from` never reads, wherever they sit: credentials the walk must not carry. */
export const CREDENTIAL_DIRS = new Set(['.ssh', '.aws', '.gnupg', '.kube', '.docker', '.azure', 'gcloud'])

const NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/
// Files that hold credentials by design: keys, dotenv, package-registry and machine logins, and
// Terraform variables and state.
export const KEY_FILES =
  /(?:^\.env(?:\..+)?$|\.(?:pem|key|p12|pfx|tfvars|tfstate)$|\.tfvars\.json$|\.tfstate\.backup$|^id_(?:rsa|dsa|ecdsa|ed25519)$|^(?:\.npmrc|\.netrc|_netrc|\.pgpass|\.pypirc|\.git-credentials)$)/i
// A value that opens like a placeholder, a lookup or a stand-in is not a secret: `<…>`, `${…}`,
// `{{…}}`, `$VAR`, `***`, `%s`, or a word such as `example` or `changeme`.
const NOT_SECRET = '[$<{*%]|(?:synthetic|example|dummy|fake|placeholder|changeme|redacted|your)'
// The same patterns tools/lint.py refuses in this repository.
const SECRETS: readonly (readonly [string, RegExp])[] = [
  ['GitHub token', /\bgh[pousr]_[A-Za-z0-9]{36,}\b/g],
  ['GitHub fine-grained token', /\bgithub_pat_[A-Za-z0-9_]{40,}\b/g],
  ['AWS access key', /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/g],
  ['private key', /-----BEGIN [A-Z ]*PRIVATE KEY-----/g],
  ['Slack token', /\bxox[abprs]-[A-Za-z0-9-]{10,}\b/g],
  ['API key', /\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}\b/g],
  ['JWT', /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g],
  ['Google API key', /\bAIza[0-9A-Za-z_-]{35}/g],
  ['Google OAuth client secret', /\bGOCSPX-[A-Za-z0-9_-]{28}/g],
  ['Stripe live key', /\b(?:sk|rk)_live_[0-9A-Za-z]{24,}/g],
  ['password in a URL', /\b[a-z][a-z0-9+.-]*:\/\/[^\s/:@<>{}$]+:(?!(?:password|passwd|pass|pwd|pw|secret|x+|\*+)@)(?![$<{%*])[^\s/@<>{}$]+@/gi],
  [
    'password',
    new RegExp(
      `(?<![A-Za-z0-9])[A-Za-z0-9_]*(?:password|passwd|client_secret|accountkey|sharedaccesskey)["']?[ \\t]*[:=][ \\t]*` +
        `(?:"(?!${NOT_SECRET})[^"\\s]{8,}"|'(?!${NOT_SECRET})[^'\\s]{8,}'` +
        `|(?!${NOT_SECRET})(?=[^\\s"'(),;&\\[\\]]*\\d)[^\\s"'(),;&\\[\\]]{8,}(?=[\\s;,&]|$))`,
      'gi',
    ),
  ],
]

const DIRECTION = new Set([0x061c, 0x200e, 0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069])
const ZERO_WIDTH = new Set([0x200b, 0x200c, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x180e, 0xfeff])
const ZWJ = 0x200d
const VS15 = 0xfe0e
const VS16 = 0xfe0f
const KEYCAP = 0x20e3
const BLACK_FLAG = 0x1f3f4
const CANCEL_TAG = 0xe007f

export type Problem = { path: string; line: number | null; column: number | null; rule: string; fix: string }

/** One entry of the walked folder, as register.ts found it. */
export type Entry = { path: string; kind: 'file'; bytes: Uint8Array } | { path: string; kind: 'link' }

export type Checked = { ok: boolean; files: Record<string, string>; count: number; bytes: number; problems: Problem[] }

const problem = (path: string, rule: string, fix: string, line: number | null = null, column: number | null = null): Problem => ({
  path,
  line,
  column,
  rule,
  fix,
})

const hex = (cp: number) => `U+${cp.toString(16).toUpperCase().padStart(4, '0')}`

/** Whether `cp` can carry an emoji presentation: pictographs, symbols and keycap bases. */
function emojiBase(cp: number): boolean {
  return (
    (cp >= 0x1f000 && cp <= 0x1faff) ||
    (cp >= 0x2600 && cp <= 0x27bf) ||
    (cp >= 0x2300 && cp <= 0x23ff) ||
    (cp >= 0x2b00 && cp <= 0x2bff) ||
    (cp >= 0x2190 && cp <= 0x21ff) ||
    (cp >= 0x2100 && cp <= 0x214f) ||
    (cp >= 0x25a0 && cp <= 0x25ff) ||
    [0x00a9, 0x00ae, 0x203c, 0x2049, 0x2934, 0x2935, 0x3030, 0x303d, 0x3297, 0x3299].includes(cp)
  )
}

/** Every credential shape in `text`, as [line, column, kind], in the order check_bundle.py finds them. */
export function credentials(text: string, cps: readonly number[] = Array.from(text, c => c.codePointAt(0) as number)): [number, number, string][] {
  const found: [number, number, string][] = []
  for (const [kind, pattern] of SECRETS) {
    for (const match of text.matchAll(pattern)) {
      const [line, column] = position(cps, codePointIndex(text, match.index ?? 0))
      found.push([line, column, kind])
    }
  }
  return found
}

/** Every character in `cps` a reader cannot see, as [index, rule, what to do]. */
export function hidden(cps: readonly number[]): [number, string, string][] {
  const found: [number, string, string][] = []
  let tagRun = false
  for (let i = 0; i < cps.length; i++) {
    const cp = cps[i] as number
    const prev = i > 0 ? cps[i - 1] : undefined
    const next = i + 1 < cps.length ? cps[i + 1] : undefined
    if (cp >= 0xe0020 && cp <= 0xe007f) {
      tagRun = tagRun || prev === BLACK_FLAG
      if (!tagRun) found.push([i, 'tag-character', `${hex(cp)} is a tag character outside a flag; delete it`])
      if (cp === CANCEL_TAG) tagRun = false
      continue
    }
    tagRun = false
    if (cp === VS15 || cp === VS16) {
      const keycap = prev !== undefined && '0123456789#*'.includes(String.fromCodePoint(prev)) && next === KEYCAP
      if (!(prev !== undefined && (emojiBase(prev) || keycap)))
        found.push([i, 'variation-selector', `${hex(cp)} follows a character that is not an emoji; delete it`])
    } else if ((cp >= 0xfe00 && cp <= 0xfe0d) || (cp >= 0xe0100 && cp <= 0xe01ef)) {
      found.push([i, 'variation-selector', `${hex(cp)} is a variation selector; delete it`])
    } else if (cp === ZWJ) {
      const before = prev !== undefined && (emojiBase(prev) || prev === VS16 || (prev >= 0x1f3fb && prev <= 0x1f3ff))
      if (!(before && next !== undefined && emojiBase(next)))
        found.push([i, 'zero-width-joiner', 'U+200D joins characters that are not emoji; delete it'])
    } else if (DIRECTION.has(cp)) {
      found.push([i, 'direction-control', `${hex(cp)} reorders the text a reader sees; delete it`])
    } else if (ZERO_WIDTH.has(cp)) {
      const where = cp === 0xfeff && i === 0 ? 'at the start of the file (a byte order mark)' : 'here'
      found.push([i, 'zero-width', `${hex(cp)} is invisible ${where}; delete it`])
    } else if (cp === 0x2028 || cp === 0x2029) {
      found.push([i, 'line-separator', `${hex(cp)} is an invisible line break; use a newline`])
    } else if ((cp < 0x20 && cp !== 0x09 && cp !== 0x0a && cp !== 0x0d) || cp === 0x7f || (cp >= 0x80 && cp <= 0x9f)) {
      found.push([i, 'control-character', `${hex(cp)} is a control character; delete it`])
    }
  }
  return found
}

/** 1-based line and column, in characters, of code point `index`. */
function position(cps: readonly number[], index: number): [number, number] {
  let line = 1
  let start = 0
  for (let i = 0; i < index; i++) {
    if (cps[i] === 0x0a) {
      line += 1
      start = i + 1
    }
  }
  return [line, index - start + 1]
}

/** The code point index of UTF-16 offset `offset` in `text`. */
function codePointIndex(text: string, offset: number): number {
  let index = 0
  for (let i = 0; i < offset; i++) {
    const unit = text.charCodeAt(i)
    if (unit >= 0xd800 && unit <= 0xdbff && i + 1 < offset) i += 1
    index += 1
  }
  return index
}

/** The text of strict UTF-8 `bytes`, or undefined when they are not UTF-8. */
export function decodeUtf8(bytes: Uint8Array): string | undefined {
  const out: number[] = []
  for (let i = 0; i < bytes.length; ) {
    const b = bytes[i] as number
    let need = 0
    let cp = 0
    let min = 0
    if (b < 0x80) {
      out.push(b)
      i += 1
      continue
    } else if (b >= 0xc2 && b <= 0xdf) {
      need = 1
      cp = b & 0x1f
      min = 0x80
    } else if (b >= 0xe0 && b <= 0xef) {
      need = 2
      cp = b & 0x0f
      min = 0x800
    } else if (b >= 0xf0 && b <= 0xf4) {
      need = 3
      cp = b & 0x07
      min = 0x10000
    } else {
      return undefined
    }
    for (let k = 1; k <= need; k++) {
      const c = bytes[i + k]
      if (c === undefined || (c & 0xc0) !== 0x80) return undefined
      cp = (cp << 6) | (c & 0x3f)
    }
    if (cp < min || cp > 0x10ffff || (cp >= 0xd800 && cp <= 0xdfff)) return undefined
    out.push(cp)
    i += need + 1
  }
  let text = ''
  for (let i = 0; i < out.length; i += 4096) text += String.fromCodePoint(...out.slice(i, i + 4096))
  return text
}

const BASE64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'

/** The bytes of standard padded base64, as `$.fs.read(path, { as: 'bytes' })` answers. */
export function fromBase64(text: string): Uint8Array {
  const clean = text.replace(/[^A-Za-z0-9+/]/g, '')
  const out = new Uint8Array(Math.floor((clean.length * 3) / 4))
  let n = 0
  for (let i = 0; i + 1 < clean.length; i += 4) {
    const at = (k: number) => (k < clean.length ? BASE64.indexOf(clean[k] as string) : -1)
    const a = at(i)
    const b = at(i + 1)
    const c = at(i + 2)
    const d = at(i + 3)
    out[n++] = (a << 2) | (b >> 4)
    if (c >= 0) out[n++] = ((b & 0x0f) << 4) | (c >> 2)
    if (d >= 0) out[n++] = ((c & 0x03) << 6) | d
  }
  return out.slice(0, n)
}

/** The top-level scalar keys of a `---` block, enough to see `name` and `description`. */
export function frontmatter(text: string): Record<string, string> | undefined {
  const lines = text.split(/\r\n|\r|\n/)
  if (lines.length === 0 || (lines[0] ?? '').trim() !== '---') return undefined
  const keys: Record<string, string> = {}
  let block: string | undefined
  for (const line of lines.slice(1)) {
    if (line.trim() === '---') return keys
    if (block && (/^[ \t]/.test(line) || !line.trim())) {
      keys[block] = `${keys[block]} ${line.trim()}`.trim()
      continue
    }
    block = undefined
    const match = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line)
    if (!match) continue
    const key = match[1] as string
    const value = (match[2] ?? '').trim()
    if (value.startsWith('|') || value.startsWith('>')) {
      keys[key] = ''
      block = key
    } else {
      keys[key] = value === '~' ? '' : value.replace(/^['"]+|['"]+$/g, '')
    }
  }
  return undefined
}

const KEY_LINE = /^(?:"[^"]*"|'[^']*'|[^\s#'"[\]{},:&*!|>%@`-][^:#]*?)\s*:(?:[ \t]+(.*)|[ \t]*)$/

/** The index just past the quoted scalar opening `value`, or undefined while it is unclosed. */
function closes(value: string, quote: string): number | undefined {
  let i = 1
  while (i < value.length) {
    const c = value[i]
    if (quote === '"' && c === '\\') {
      i += 2
      continue
    }
    if (c === quote) {
      if (quote === "'" && value[i + 1] === "'") {
        i += 2
        continue
      }
      return i + 1
    }
    i += 1
  }
  return undefined
}

/** Why a top-level value, with any continuation lines joined, is not YAML the registry reads. */
function valueProblem(value: string): string | undefined {
  const v = value.trim()
  if (!v || v[0] === '#') return undefined
  const first = v[0] as string
  if (first === '"' || first === "'") {
    const end = closes(v, first)
    if (end === undefined) return `the ${first} quote is never closed`
    const rest = v.slice(end).trim()
    return rest && !rest.startsWith('#') ? 'text follows the closing quote; quote the whole value' : undefined
  }
  if (first === '[' || first === '{') {
    let depth = 0
    let quote: string | undefined
    for (let i = 0; i < v.length; i++) {
      const c = v[i] as string
      if (quote) {
        if (c === quote) quote = undefined
      } else if (c === '"' || c === "'") quote = c
      else if (c === '[' || c === '{') depth += 1
      else if (c === ']' || c === '}') {
        depth -= 1
        if (depth === 0) {
          const rest = v.slice(i + 1).trim()
          return !rest || rest.startsWith('#') ? undefined : 'text follows the closing bracket'
        }
      }
    }
    return `the ${first} is never closed`
  }
  if (first === '|' || first === '>') return /^[|>][-+0-9]*\s*(?:#.*)?$/.test(v) ? undefined : "a block scalar's indicator line holds text"
  if (first === '@' || first === '`') return `a plain value cannot start with ${first}; quote it`
  const plain = v.split(/\s#/)[0] as string
  if (/:(?:\s|$)/.test(plain)) return 'a plain value holds `: `; put the value in double quotes or use `>`'
  return undefined
}

/**
 * What the registry's parser refuses in SKILL.md's frontmatter and body, as [line, column, rule,
 * fix]: check_bundle.py's frontmatter_problems, line for line. The subset of YAML a SKILL.md
 * uses, not the whole language.
 */
export function frontmatterProblems(text: string): [number, number, string, string][] {
  const lines = text.split(/\r\n|\r|\n/)
  if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
  let end = -1
  for (let i = 1; i < lines.length; i++) {
    if ((lines[i] as string).trim() === '---') {
      end = i
      break
    }
  }
  if (lines.length === 0 || (lines[0] as string).trim() !== '---' || end < 0) return []
  const found: [number, number, string, string][] = []
  const seen = new Map<string, number>()
  const block = lines.slice(1, end)
  let i = 0
  while (i < block.length) {
    const line = block[i] as string
    const number = i + 2
    i += 1
    if (!line.trim() || line.trimStart().startsWith('#')) continue
    const indent = line.slice(0, line.length - line.replace(/^[ \t]+/, '').length)
    if (indent.includes('\t')) {
      found.push([number, indent.indexOf('\t') + 1, 'frontmatter-yaml', 'a tab indents this line; YAML indents with spaces'])
      continue
    }
    if (indent) continue
    const match = KEY_LINE.exec(line)
    if (!match) {
      found.push([number, 1, 'frontmatter-yaml', 'is not a `key: value` line; fix it or indent it under its key'])
      continue
    }
    const key = (line.split(':', 1)[0] as string).trim().replace(/^["']+|["']+$/g, '')
    const first = seen.get(key)
    if (first !== undefined) found.push([number, 1, 'frontmatter-yaml', `\`${key}\` is given twice (first on line ${first}); keep one`])
    else seen.set(key, number)
    let value = match[1] ?? ''
    while (i < block.length && /^[ \t]/.test(block[i] as string) && ['"', "'", '[', '{'].includes(value.trim()[0] ?? '')) {
      value += ` ${(block[i] as string).trim()}`
      i += 1
    }
    const why = valueProblem(value)
    if (why) found.push([number, line.indexOf(':') + 2, 'frontmatter-yaml', why])
  }
  if (!lines.slice(end + 1).join('\n').trim())
    found.push([end + 1, 1, 'no-body', 'nothing follows the frontmatter; the registry needs Markdown instructions after the closing `---`'])
  return found
}

/** Why `rel` is not a path the create takes, or undefined. */
export function pathProblem(rel: string): string | undefined {
  const parts = rel.split('/')
  if (parts.length > MAX_DEPTH) return `is ${parts.length} levels deep; the create takes at most ${MAX_DEPTH}`
  if (parts.some(part => part === '' || part === '.' || part === '..') || rel.includes('\\') || rel.includes(':'))
    return 'is not a plain relative path; rename it'
  if ([...rel].some(c => c.charCodeAt(0) < 0x20 || c.charCodeAt(0) === 0x7f)) return 'has a control character in its name; rename it'
  return undefined
}

/** Whether the walk leaves an entry named `name` out, as check_bundle.py does. */
export const isSkipped = (name: string, isDir: boolean): boolean => (isDir ? SKIPPED_DIRS.has(name) : SKIPPED_FILES.has(name))

/**
 * Check one walked folder, or several (`label` prefixes each path when there are several), and
 * build the `files` map the create takes. The limits are per call, so `check` takes every folder
 * of one call at once.
 */
export function check(folders: readonly { label: string; entries: readonly Entry[] }[], requireSkill = true): Checked & { byFolder: Record<string, string>[] } {
  const problems: Problem[] = []
  const byFolder: Record<string, string>[] = []
  let count = 0
  let total = 0
  for (const folder of folders) {
    const files: Record<string, string> = {}
    byFolder.push(files)
    const label = (rel: string) => (folders.length === 1 ? rel : `${folder.label}/${rel}`)
    const root = folder.entries.find(entry => entry.path === 'SKILL.md')
    if (requireSkill && (!root || root.kind !== 'file')) problems.push(problem(label('SKILL.md'), 'missing-skill-md', `${folder.label} has no SKILL.md at its root`))
    for (const entry of [...folder.entries].sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0))) {
      const where = label(entry.path)
      if (entry.kind === 'link') {
        problems.push(problem(where, 'symbolic-link', 'is a symbolic link; copy the file in, or use the ZIP route'))
        continue
      }
      count += 1
      total += entry.bytes.length
      const bad = pathProblem(entry.path)
      if (bad) problems.push(problem(where, 'unsafe-path', `the path ${bad}`))
      const base = entry.path.slice(entry.path.lastIndexOf('/') + 1)
      if (KEY_FILES.test(base)) {
        problems.push(problem(where, 'credential-file', 'looks like a credential file; leave it out'))
        continue
      }
      const text = decodeUtf8(entry.bytes)
      if (text === undefined) {
        problems.push(problem(where, 'not-text', 'is not UTF-8 text; send this bundle through the ZIP route'))
        continue
      }
      const cps = Array.from(text, c => c.codePointAt(0) as number)
      for (const [index, rule, fix] of hidden(cps)) {
        const [line, column] = position(cps, index)
        problems.push(problem(where, rule, fix, line, column))
      }
      for (const [line, column, kind] of credentials(text, cps)) problems.push(problem(where, 'credential', `matches the ${kind} pattern; remove it`, line, column))
      if (entry.path === 'SKILL.md') {
        const keys = frontmatter(text)
        if (keys === undefined) {
          problems.push(problem(where, 'frontmatter', 'needs a `---` block with `name` and `description` at the top', 1, 1))
        } else {
          const name = keys.name ?? ''
          const description = keys.description ?? ''
          if (!NAME.test(name) || name.length > 64)
            problems.push(problem(where, 'frontmatter-name', '`name` must be lowercase letters, digits and single hyphens, at most 64', 1, 1))
          if (!description)
            problems.push(problem(where, 'frontmatter-description', '`description` is missing; say what the skill does and when to use it', 1, 1))
          else if (description.length > 1024) problems.push(problem(where, 'frontmatter-description', '`description` is over 1024 characters', 1, 1))
          for (const [line, column, rule, fix] of frontmatterProblems(text)) problems.push(problem(where, rule, fix, line, column))
        }
      }
      files[entry.path] = text
    }
  }
  if (count > MAX_FILES)
    problems.push(problem('', 'too-many-files', `${count} files; inline \`files\` take at most ${MAX_FILES} a call, so use the ZIP route`))
  if (total > MAX_BYTES)
    problems.push(problem('', 'too-large', `${total} bytes; inline \`files\` take at most ${MAX_BYTES} a call, so use the ZIP route`))
  const merged = Object.assign({}, ...byFolder) as Record<string, string>
  return { ok: problems.length === 0, files: merged, byFolder, count, bytes: total, problems }
}

/** The problems as check_bundle.py prints them, one line each, then the verdict. */
export function render(result: Pick<Checked, 'ok' | 'count' | 'bytes' | 'problems'>): string {
  const lines = result.problems.map(p => {
    const where = [p.path, p.line, p.column].filter(part => part !== null && part !== '').join(':')
    return where ? `${where}: ${p.rule}: ${p.fix}` : `${p.rule}: ${p.fix}`
  })
  const verdict = result.ok ? 'ready to send' : `${result.problems.length} problem(s); fix them before sending`
  lines.push(`${result.count} files, ${result.bytes} bytes: ${verdict}`)
  return lines.join('\n')
}
