import type { On } from 'claude-code'
import { describe, expect, mock, test } from 'claude-code/testing'

import { CAPABILITY, ROUTING, BULK_CAPABILITY, RECEIPT_CAPABILITY, isKeyed, isLostReply } from '../register'
import type { Case } from './case'
import { CASES } from './fixtures'

const CALL = 'mcp__plugin_atlanai_atlan__mcp__call_tool'
const PUBLISH = 'extension__skill__create'
const CWD = '/work'
const BULK = 'registry__create_artifacts'
// The fixtures' stand-in, built here so no credential-shaped string is committed.
const FAKE_AWS_KEY = 'AKIA' + 'ABCDEFGHIJKLMNOP'
const SKILL = '---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n'

type Node = { kind: 'file'; bytes: Uint8Array } | { kind: 'dir' } | { kind: 'link'; target: string }

const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
function toBase64(bytes: Uint8Array): string {
  let out = ''
  for (let i = 0; i < bytes.length; i += 3) {
    const [a = 0, b = 0, c = 0] = [bytes[i], bytes[i + 1], bytes[i + 2]]
    const n = (a << 16) | (b << 8) | c
    out += B64[(n >> 18) & 63]! + B64[(n >> 12) & 63]!
    out += i + 1 < bytes.length ? B64[(n >> 6) & 63]! : '='
    out += i + 2 < bytes.length ? B64[n & 63]! : '='
  }
  return out
}

function norm(path: string): string {
  const parts: string[] = []
  for (const part of path.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') parts.pop()
    else parts.push(part)
  }
  return `/${parts.join('/')}`
}

const hexBytes = (hex: string) => Uint8Array.from(hex.match(/../g) ?? [], pair => parseInt(pair, 16))

/** A file system in memory: what the hook may read, and every path it asked for. */
class Disk {
  nodes = new Map<string, Node>([['/', { kind: 'dir' }]])
  asked: string[] = []

  add(path: string, node: Node) {
    const full = norm(path)
    const parts = full.split('/').filter(Boolean)
    for (let i = 1; i < parts.length; i++) {
      const dir = `/${parts.slice(0, i).join('/')}`
      if (!this.nodes.has(dir)) this.nodes.set(dir, { kind: 'dir' })
    }
    this.nodes.set(full, node)
  }

  file(path: string, text: string) {
    this.add(path, { kind: 'file', bytes: new TextEncoder().encode(text) })
  }

  /** Where `path` lands, following links in every segment, or undefined. */
  real(path: string, depth = 0): string | undefined {
    if (depth > 16) return undefined
    let at = '/'
    for (const part of norm(path).split('/').filter(Boolean)) {
      const next = norm(`${at}/${part}`)
      const node = this.nodes.get(next)
      if (!node) return undefined
      if (node.kind === 'link') {
        const landed = this.real(node.target.startsWith('/') ? node.target : `${at}/${node.target}`, depth + 1)
        if (!landed) return undefined
        at = landed
      } else {
        at = next
      }
    }
    return at
  }

  /** The entry `path` names itself, a link not followed: its folder resolved, its name looked up. */
  own(path: string): Node | undefined {
    const full = norm(path)
    const cut = full.lastIndexOf('/')
    const parent = this.real(full.slice(0, cut) || '/')
    return parent === undefined ? undefined : this.nodes.get(norm(`${parent}/${full.slice(cut + 1)}`))
  }

  hook(on: On) {
    mock.clock(on, { now: Date.UTC(2026, 10, 3, 10, 0, 0) })
    on('session.cwd', () => ({ value: CWD }))
    on('session.root', () => ({ value: CWD }))
    on('fs.stat', (_, e) => {
      this.asked.push(e.path)
      const own = this.own(e.path)
      const real = this.real(e.path)
      const target = real ? this.nodes.get(real) : undefined
      if (!own || !target) throw new Error(`ENOENT: ${e.path}`)
      const kind = target.kind === 'file' ? 'file' : target.kind === 'dir' ? 'dir' : 'other'
      const size = target.kind === 'file' ? target.bytes.length : 0
      return { value: { kind, size, mtimeMs: 0, isLink: own.kind === 'link', ...(e.resolve ? { realPath: real } : {}) } }
    })
    on('fs.list', (_, e) => {
      this.asked.push(e.path)
      const dir = norm(e.path)
      if (this.nodes.get(dir)?.kind !== 'dir') throw new Error(`ENOTDIR: ${e.path}`)
      const value = [...this.nodes]
        .filter(([path]) => path !== dir && path.slice(0, path.lastIndexOf('/') || 1) === dir)
        .map(([path, node]) => ({
          name: path.slice(path.lastIndexOf('/') + 1),
          kind: node.kind === 'link' ? ('other' as const) : node.kind,
          size: node.kind === 'file' ? node.bytes.length : 0,
          mtimeMs: 0,
          isLink: node.kind === 'link',
        }))
      return { value }
    })
    on('fs.read', (_, e) => {
      this.asked.push(e.path)
      const real = this.real(e.path)
      const node = real ? this.nodes.get(real) : undefined
      if (node?.kind !== 'file') throw new Error(`ENOENT: ${e.path}`)
      return { value: { base64: toBase64(node.bytes) } }
    })
  }
}

/** A case's folder, built at `base` the way check_bundle.py's tests build it. */
function build(disk: Disk, base: string, one: Case) {
  disk.add(base, { kind: 'dir' })
  for (const [rel, text] of Object.entries(one.files ?? {})) disk.file(`${base}/${rel}`, text.replaceAll('{{fake_aws_key}}', FAKE_AWS_KEY).replaceAll('{{split}}', ''))
  for (const [rel, hex] of Object.entries(one.binary ?? {})) disk.add(`${base}/${rel}`, { kind: 'file', bytes: hexBytes(hex) })
  for (const [rel, target] of Object.entries(one.links ?? {})) disk.add(`${base}/${rel}`, { kind: 'link', target })
  const generate = one.generate ?? {}
  for (let n = 0; n < (generate.files ?? 0); n++) disk.file(`${base}/filler/${String(n).padStart(3, '0')}.md`, 'filler\n')
  if (generate.pad_to_bytes !== undefined) {
    let used = 0
    for (const [path, node] of disk.nodes) if (path.startsWith(`${base}/`) && node.kind === 'file') used += node.bytes.length
    disk.file(`${base}/pad.md`, 'x'.repeat(generate.pad_to_bytes - used))
  }
}

/** The engine beneath the plugin: the disk, and the gateway, which records what reached it. */
function engine(on: On, disk: Disk) {
  disk.hook(on)
  const sent: Record<string, unknown>[] = []
  on('tool.call', (_, e) => {
    sent.push({ ...e })
    return { result: 'ok', text: JSON.stringify({ created: [{ name: 'demo', artifact_status: 'active' }] }) }
  })
  return sent
}

const publish = (skill: Record<string, unknown>) => ({
  tool: CALL,
  name: PUBLISH,
  rationale: 'Publishing the demo skill.',
  arguments: { skills: [{ name: 'demo', workspace_id: 'workspace_01ws', ...skill }], wait_for_release: true },
})

function skillsOf(call: Record<string, unknown> | undefined): Record<string, unknown>[] {
  const args = call?.arguments as { skills?: Record<string, unknown>[] } | undefined
  return args?.skills ?? []
}

async function bulkCall(text: string, path = 'payload.json', changes: Record<string, unknown> = {}) {
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))
  return { tool: CALL, name: BULK, rationale: 'Publish the checked definitions.', arguments: {
    kind: 'sql_query', workspace_id: 'workspace_example', items: [], items_from: path,
    items_sha256: Array.from(digest, b => b.toString(16).padStart(2, '0')).join(''), ...changes,
  } }
}

const batchItem = { name: 'example-query', source_id: 'example-query', workspace_id: 'workspace_example',
  definition: { sql: 'SELECT\n  COUNT(*)\nFROM example_orders', question: 'How many orders are there?' } }

describe('deterministic receipt capture', () => {
  test('strips the local destination and records the native result without a second tool call', async ($, on) => {
    const disk = new Disk()
    disk.add(CWD, { kind: 'dir' })
    const sent = engine(on, disk)
    const processes: string[] = []
    on('process.run', (_, e) => {
      processes.push(JSON.stringify(e))
      return { value: { exitCode: 0, stdout: JSON.stringify({ ready: true, recorded: true, path: '/work/run/receipts/example.json' }), stderr: '' } }
    })
    const ran = await $.tool.call({ tool: CALL.replace('call_tool', 'call_read_tool'), name: 'registry__get_artifact',
      arguments: { artifact_id: 'metric_example', channel: 'latest', receipt_dir: '/work/run/receipts' } } as never)
    expect(sent.length).toBe(1)
    expect(sent[0]?.arguments).toEqual({ artifact_id: 'metric_example', channel: 'latest' })
    expect(processes.length).toBe(2)
    expect(processes[1]).toContain('response_text')
    expect(JSON.stringify(ran)).toContain('Atlan receipt saved')
  })

  test('reuses the resolved interpreter while retaining every preflight and capture', async ($, on) => {
    const disk = new Disk()
    disk.add(CWD, { kind: 'dir' })
    const sent = engine(on, disk)
    const processes: string[] = []
    on('process.run', (_, e) => {
      processes.push(JSON.stringify(e))
      return { value: { exitCode: 0, stdout: JSON.stringify({ ready: true, recorded: true, python_executable: '/runtime/bin/python3' }), stderr: '' } }
    })
    for (const id of ['metric_first', 'metric_second']) await $.tool.call({ tool: CALL, name: 'registry__get_artifact',
      arguments: { artifact_id: id, channel: 'latest', receipt_dir: '/work/run/receipts' } } as never)
    expect(sent.length).toBe(2)
    expect(processes.length).toBe(4)
    expect(processes[0]).not.toContain('/runtime/bin/python3')
    for (const process of processes.slice(1)) expect(process).toContain('/runtime/bin/python3')
    expect(processes.filter(p => p.includes('response_text')).length).toBe(2)
  })

  test('keeps an executed result and never replays it when recording fails', async ($, on) => {
    const disk = new Disk()
    disk.add(CWD, { kind: 'dir' })
    const sent = engine(on, disk)
    let attempts = 0
    on('process.run', () => ({ value: { exitCode: ++attempts === 1 ? 0 : 1, stdout: '{"ready":true}', stderr: '' } }))
    const ran = await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: {
      artifact_id: 'metric_example', channel: 'latest', receipt_dir: '/work/run/receipts' } } as never)
    expect(sent.length).toBe(1)
    expect(JSON.stringify(ran)).toContain('The tool already ran')
    expect(JSON.stringify(ran)).toContain('never repeat a write')
  })

  test('refuses a bad receipt destination before dispatching the tool', async ($, on) => {
    const disk = new Disk()
    disk.add(CWD, { kind: 'dir' })
    const sent = engine(on, disk)
    on('process.run', () => ({ value: { exitCode: 1, stdout: '{"recorded":false}', stderr: '' } }))
    await $.tool.call({ tool: CALL, name: 'registry__get_artifact', arguments: {
      artifact_id: 'metric_example', channel: 'latest', receipt_dir: '/outside/receipts' } } as never)
    expect(sent.length).toBe(0)
  })
})

describe('local bulk publication', () => {
  test('loads exact SQL for metrics and saved queries without changing audit context', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    const text = JSON.stringify([batchItem])
    disk.file(`${CWD}/payload.json`, text)
    for (const kind of ['metric', 'sql_query']) await $.tool.call(await bulkCall(text, 'payload.json', { kind }) as never)
    expect(sent.length).toBe(2)
    for (const call of sent) {
      expect((call.arguments as { items: unknown }).items).toEqual([batchItem])
      expect(Object.keys(call.arguments as object).sort()).toEqual(['items', 'kind'])
      expect(call.rationale).toBe('Publish the checked definitions.')
    }
    expect(disk.asked.some(path => path.includes('.env'))).toBe(false)
  })

  test('refuses changed digests, mixed workspaces, inline items and malformed JSON', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    const text = JSON.stringify([batchItem])
    disk.file(`${CWD}/payload.json`, text)
    for (const changes of [{ items_sha256: '0'.repeat(64) }, { workspace_id: 'workspace_other' }, { items: [batchItem] }, { kind: 'agent' }]) {
      await $.tool.call(await bulkCall(text, 'payload.json', changes) as never)
    }
    for (const invalid of ['{ private malformed content', '[]', JSON.stringify([batchItem, batchItem]), JSON.stringify([{ ...batchItem, workspace_id: 'workspace_other' }])]) {
      disk.file(`${CWD}/payload.json`, invalid)
      await $.tool.call(await bulkCall(invalid) as never)
    }
    expect(sent.length).toBe(0)
  })

  test('refuses links, outside paths and credential paths before reading bytes', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    const text = JSON.stringify([batchItem])
    disk.file('/outside/payload.json', text)
    disk.add(`${CWD}/linked`, { kind: 'link', target: '/outside' })
    disk.file(`${CWD}/.aws/payload.json`, text)
    disk.file(`${CWD}/.env.json`, text)
    for (const path of ['/outside/payload.json', 'linked/payload.json', '.aws/payload.json', '.env.json', '../outside/payload.json']) {
      await $.tool.call(await bulkCall(text, path) as never)
    }
    expect(sent.length).toBe(0)
    expect(disk.asked.some(path => path.startsWith('/outside'))).toBe(false)
  })

  test('scans decoded JSON strings and rejects payloads above the limit', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    const token = 'sk-' + 'a'.repeat(35)
    const secret = JSON.stringify([{ ...batchItem, description: token }]).replace(token, '\\u0073' + token.slice(1))
    for (const text of [secret, ' '.repeat(256 * 1024 + 1)]) {
      disk.file(`${CWD}/payload.json`, text)
      await $.tool.call(await bulkCall(text) as never)
    }
    expect(sent.length).toBe(0)
  })

  test('leaves an unhooked empty batch unable to write and ignores other servers', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    const call = await bulkCall(JSON.stringify([batchItem]))
    await $.tool.call({ ...call, tool: 'mcp__other__mcp__call_tool' } as never)
    expect(sent.length).toBe(1)
    expect((sent[0]?.arguments as { items: unknown }).items).toEqual([])
    expect(disk.asked.length).toBe(0)
  })
})

describe('the shared bundle fixtures', () => {
  for (const one of CASES) {
    test(one.name, async ($, on) => {
      const disk = new Disk()
      const sent = engine(on, disk)
      const isHookCase = one.applies_to?.includes('hook') === true
      if (isHookCase) {
        build(disk, CWD, { name: one.name, expect: 'send', files: { 'SKILL.md': SKILL } })
        disk.file('/etc/SKILL.md', SKILL)
        disk.file(`${CWD}/.ssh/SKILL.md`, SKILL)
        disk.file(`${CWD}/.ssh/id_ed25519`, 'not a real key')
      } else {
        build(disk, `${CWD}/demo`, one)
      }
      const ran = await $.tool.call(publish(isHookCase ? { ...one.call } : { files_from: 'demo' }) as never)
      const text = String((ran as { deny?: string }).deny ?? (ran as { text?: string }).text)
      expect(text.includes(FAKE_AWS_KEY)).toBe(false)
      if (one.expect === 'refuse') {
        expect(sent.length).toBe(0)
        expect(text).toContain('Nothing was sent')
        for (const rule of one.rules ?? []) expect(text).toContain(`${rule}: `)
        for (const [path, line, column] of one.at ?? []) expect(text).toContain(`${path}:${line}:${column}: `)
        return
      }
      expect(sent.length).toBe(1)
      const [skill] = skillsOf(sent[0])
      expect(skill?.files_from).toBe(undefined)
      const files = (skill?.files ?? {}) as Record<string, string>
      const expected = one.sent ?? [...disk.nodes.keys()].filter(path => path.startsWith(`${CWD}/demo/`) && disk.nodes.get(path)?.kind === 'file').map(path => path.slice(`${CWD}/demo/`.length))
      expect(Object.keys(files).sort()).toEqual([...expected].sort())
      for (const [rel, body] of Object.entries(one.files ?? {})) if (rel in files) expect(files[rel]).toBe(body)
      const digests = (skill?.sha256 ?? {}) as Record<string, string>
      expect(Object.keys(digests).sort()).toEqual(Object.keys(files).sort())
      for (const digest of Object.values(digests)) expect(/^[0-9a-f]{64}$/.test(digest)).toBe(true)
    })
  }
})

describe('files_from', () => {
  test('reads nothing outside the folder it names', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    disk.file(`${CWD}/notes.md`, 'not part of the skill')
    disk.file(`${CWD}/.env`, 'TOKEN=placeholder')
    await $.tool.call(publish({ files_from: 'demo' }) as never)
    expect(sent.length).toBe(1)
    const reads = disk.asked.filter(path => !path.startsWith(`${CWD}/demo`) && path !== CWD && path !== 'demo')
    expect(reads).toEqual([])
  })

  test('refuses a folder that is a link out of the working tree', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file('/home/me/.aws/SKILL.md', SKILL)
    disk.add(`${CWD}/demo`, { kind: 'link', target: '/home/me/.aws' })
    const ran = await $.tool.call(publish({ files_from: 'demo' }) as never)
    expect(sent.length).toBe(0)
    expect(String((ran as { deny?: string }).deny ?? (ran as { text?: string }).text)).toContain('symbolic-link')
    expect(disk.asked.some(path => path.startsWith('/home/me'))).toBe(false)
  })

  test('refuses an absolute path that resolves outside the working tree', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file('/home/me/skill/SKILL.md', SKILL)
    disk.add(`${CWD}/inside`, { kind: 'link', target: '/home/me' })
    const ran = await $.tool.call(publish({ files_from: `${CWD}/inside/skill` }) as never)
    expect(sent.length).toBe(0)
    expect(String((ran as { deny?: string }).deny ?? (ran as { text?: string }).text)).toContain('inside the working tree')
  })

  test('refuses a credential folder inside the skill without reading it', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    disk.file(`${CWD}/demo/.AWS/credentials`, 'placeholder')
    const ran = await $.tool.call(publish({ files_from: 'demo' }) as never)
    expect(sent.length).toBe(0)
    expect(String((ran as { deny?: string }).deny ?? (ran as { text?: string }).text)).toContain('.AWS: credential-folder')
    expect(disk.asked.some(path => path.includes('.AWS/'))).toBe(false)
  })

  test('expands several skills at once, the limits counted across the call', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file(`${CWD}/one/SKILL.md`, SKILL)
    disk.file(`${CWD}/two/SKILL.md`, SKILL.replace('name: demo', 'name: two'))
    const call = publish({ files_from: 'one' })
    call.arguments.skills.push({ name: 'two', workspace_id: 'workspace_01ws', files_from: 'two' } as never)
    await $.tool.call(call as never)
    expect(sent.length).toBe(1)
    const skills = skillsOf(sent[0])
    expect(skills.map(skill => Object.keys(skill.files as object))).toEqual([['SKILL.md'], ['SKILL.md']])
    expect(String((skills[1]?.files as Record<string, string>)['SKILL.md'])).toContain('name: two')
  })

  test("expands under the write runner too, and never under another server's call_tool", async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    await $.tool.call({ ...publish({ files_from: 'demo' }), tool: 'mcp__plugin_atlanai_atlan__mcp__call_write_tool' } as never)
    await $.tool.call({ ...publish({ files_from: 'demo' }), tool: 'mcp__unrelated_vendor__call_tool' } as never)
    expect(sent.length).toBe(2)
    expect(Object.keys(skillsOf(sent[0])[0]?.files as object)).toEqual(['SKILL.md'])
    expect(skillsOf(sent[1])[0]?.files_from).toBe('demo')
    expect(skillsOf(sent[1])[0]?.files).toBe(undefined)
  })

  test("counts the other skills' inline files in the call's one budget", async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    disk.file(`${CWD}/demo/references/big.md`, 'x'.repeat(150 * 1024))
    const call = publish({ files_from: 'demo' })
    call.arguments.skills.push({ name: 'two', workspace_id: 'workspace_01ws', files: { 'SKILL.md': SKILL + 'y'.repeat(150 * 1024) } } as never)
    const ran = (await $.tool.call(call as never)) as { deny?: string }
    expect(sent.length).toBe(0)
    expect(String(ran.deny)).toContain('too-large: ')
    expect(String(ran.deny)).toContain('Nothing was sent')
  })

  test('takes arguments sent as a JSON string, and sends them back the same way', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    const call = publish({ files_from: 'demo' })
    await $.tool.call({ ...call, arguments: JSON.stringify(call.arguments) } as never)
    expect(sent.length).toBe(1)
    const args = JSON.parse(String(sent[0]?.arguments)) as { skills: Record<string, unknown>[] }
    expect(Object.keys(args.skills[0]?.files as object)).toEqual(['SKILL.md'])
  })

  test("sends each file's sha256 with its text, replacing any the call carried", async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    disk.file(`${CWD}/demo/notes.md`, 'abc')
    disk.file(`${CWD}/demo/empty.md`, '')
    await $.tool.call(publish({ files_from: 'demo', sha256: { 'notes.md': '0'.repeat(64), 'gone.md': '1'.repeat(64) } }) as never)
    expect(sent.length).toBe(1)
    const [skill] = skillsOf(sent[0])
    const digests = skill?.sha256 as Record<string, string>
    expect(Object.keys(digests).sort()).toEqual(['SKILL.md', 'empty.md', 'notes.md'])
    // The FIPS 180-2 vectors for "abc" and the empty string.
    expect(digests['notes.md']).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    expect(digests['empty.md']).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')
  })

  test('sends no sha256 for a skill that names files itself', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    const call = publish({ files_from: 'demo' })
    call.arguments.skills.push({ name: 'two', workspace_id: 'workspace_01ws', files: { 'SKILL.md': SKILL } } as never)
    await $.tool.call(call as never)
    const skills = skillsOf(sent[0])
    expect(Object.keys(skills[0]?.sha256 as object)).toEqual(['SKILL.md'])
    expect(skills[1]?.sha256).toBe(undefined)
  })

  test('leaves a call without files_from as it was', async ($, on) => {
    const disk = new Disk()
    const sent = engine(on, disk)
    const call = publish({ files: { 'SKILL.md': SKILL } })
    await $.tool.call(call as never)
    expect(skillsOf(sent[0])).toEqual(call.arguments.skills)
    expect(disk.asked).toEqual([])
  })

  test('passes the call on unchanged when the walk fails, so the server refuses files_from', async ($, on) => {
    const sent: Record<string, unknown>[] = []
    on('session.cwd', () => {
      throw new Error('no session')
    })
    on('tool.call', (_, e) => {
      sent.push({ ...e })
      return { result: 'ok', text: '{}' }
    })
    await $.tool.call(publish({ files_from: 'demo' }) as never)
    expect(sent.length).toBe(1)
    expect(skillsOf(sent[0])[0]?.files_from).toBe('demo')
  })
})

describe('a lost reply', () => {
  test('tells the model to read back before sending the create again', async ($, on) => {
    const disk = new Disk()
    disk.hook(on)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    let calls = 0
    on('tool.call', () => {
      calls += 1
      return { result: 'error', isError: true, text: JSON.stringify({ error: { code: -32603, data: { may_have_run: true, retryable: false } } }) }
    })
    const ran = (await $.tool.call(publish({ files_from: 'demo' }) as never)) as { context?: readonly string[] }
    expect(calls).toBe(1)
    const note = (ran.context ?? []).join('\n')
    expect(note).toContain('registry__search_artifacts')
    expect(note).toContain('no idempotency_key')
    expect(note).toContain('include_unreviewed: true')
  })

  test('tells the model to resend unchanged when every skill carried a key', async ($, on) => {
    const disk = new Disk()
    disk.hook(on)
    disk.file(`${CWD}/demo/SKILL.md`, SKILL)
    on('tool.call', () => ({ result: 'error', isError: true, text: '503 service unavailable' }))
    const ran = (await $.tool.call(publish({ files_from: 'demo', idempotency_key: 'demo-q8w2m4z1' }) as never)) as {
      context?: readonly string[]
    }
    const note = (ran.context ?? []).join('\n')
    expect(note).toContain('send the same call again, unchanged')
    expect(note).not.toContain('registry__search_artifacts')
  })

  test('counts a call as keyed only when every skill has a key', () => {
    expect(isKeyed({ skills: [{ idempotency_key: 'a-1' }, { idempotency_key: 'b-1' }] })).toBe(true)
    expect(isKeyed({ skills: [{ idempotency_key: 'a-1' }, {}] })).toBe(false)
    expect(isKeyed({ skills: [{ idempotency_key: '' }] })).toBe(false)
    expect(isKeyed({ skills: [] })).toBe(false)
    expect(isKeyed({})).toBe(false)
  })

  test('is a may-have-run or an unavailable answer, never a refusal', () => {
    expect(isLostReply('{"may_have_run":true,"retryable":false}')).toBe(true)
    expect(isLostReply('503 service unavailable')).toBe(true)
    expect(isLostReply('{"code":"conflict","retryable":false} 409')).toBe(false)
    expect(isLostReply('{"code":"validation_failed"}')).toBe(false)
  })
})

describe('the capability line', () => {
  const prompt = (text: string, extra: Record<string, unknown> = {}) => ({ text, wait: false, origin: { kind: 'composer' as const }, ...extra })

  test('rides the first prompt of a conversation once, and again after /clear', async ($, on) => {
    let session = 'session-1'
    on('session.id', () => ({ value: session }))
    const contexts: (readonly string[] | undefined)[] = []
    on('prompt.submit', (_, e) => {
      contexts.push(e.context)
      return { text: e.text, context: e.context }
    })
    await $.prompt.submit(prompt('publish my skill'))
    await $.prompt.submit(prompt('and another'))
    session = 'session-2'
    await $.prompt.submit(prompt('after clear'))
    expect(contexts).toEqual([[CAPABILITY, ROUTING, BULK_CAPABILITY, RECEIPT_CAPABILITY], undefined, [CAPABILITY, ROUTING, BULK_CAPABILITY, RECEIPT_CAPABILITY]])
  })

  test('keeps the context a prompt already carries', async ($, on) => {
    on('session.id', () => ({ value: 'session-1' }))
    const contexts: (readonly string[] | undefined)[] = []
    on('prompt.submit', (_, e) => {
      contexts.push(e.context)
      return { text: e.text, context: e.context }
    })
    await $.prompt.submit(prompt('publish my skill', { context: ['from another hook'] }))
    expect(contexts).toEqual([['from another hook', CAPABILITY, ROUTING, BULK_CAPABILITY, RECEIPT_CAPABILITY]])
  })

  test('adds nothing to the system prompt', async ($, on) => {
    on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'You are Claude.', scope: 'shared' as const }] }))
    const composed = await $.prompt.compose({ model: 'm', promptModel: 'm', surfaces: [], tools: [], outputStyle: null, traits: [] })
    expect(composed.sections).toEqual([{ id: 'intro', text: 'You are Claude.', scope: 'shared' }])
  })
})
