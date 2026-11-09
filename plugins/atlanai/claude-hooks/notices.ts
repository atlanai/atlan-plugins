// Two one-line notices the core's hooks module adds beside the model's input, each at most once a
// conversation, and each failing silent: the update notice, when this install is older than the
// latest release, and the feedback offer, after an Atlan call fails in a way atlan-troubleshoot
// may not fix or, now and then, after a turn that used Atlan. Neither sends anything about the
// user: the update check reads one public file, and feedback is drafted by the model, shown to
// the user and sent only by them (atlan-troubleshoot/references/feedback.md).
//
// Plain functions, so the tests can hold each rule without the engine.

/** Any tool of the `atlan` server, under the plugin's prefix or added by hand. */
export const ATLAN_TOOL = /^mcp__(?:plugin_[A-Za-z0-9_-]+_)?atlan__/

/** The share of turns after Atlan work that get the feedback offer: one in fifty. */
export const FEEDBACK_SAMPLE_RATE = 0.02

/** How long the first prompt waits for the latest version before going on without it. */
export const UPDATE_WAIT_MS = 1500

const REFERENCE = "atlan-troubleshoot's references/feedback.md"

export const FEEDBACK_ON_FAILURE =
  'An Atlan call failed. Fix it with atlan-troubleshoot first. If no row there fixes it, or the fix is a workaround, ' +
  `offer the user once to draft feedback for Atlan, following ${REFERENCE}: show the draft and send nothing without ` +
  "their yes. Then continue the user's task."

export const FEEDBACK_SAMPLED =
  'Before answering the new message: if the Atlan skills or tools in the last turn fell short or did surprisingly well, ' +
  `offer the user once to draft feedback for Atlan, following ${REFERENCE}, and send nothing without their yes. If ` +
  "nothing stood out, say nothing about it. Then respond to the user's new message."

type Version = readonly [number, number, number]

/** `x.y.z` as numbers; a pre-release or anything else is not compared. */
export function parseVersion(text: unknown): Version | undefined {
  const match = typeof text === 'string' ? /^(\d+)\.(\d+)\.(\d+)$/.exec(text.trim()) : null
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : undefined
}

/** Whether `latest` is a later release than `installed`. Unreadable versions never are. */
export function isNewer(latest: unknown, installed: unknown): boolean {
  const [a, b] = [parseVersion(latest), parseVersion(installed)]
  if (!a || !b) return false
  for (let i = 0; i < 3; i++) if (a[i] !== b[i]) return a[i]! > b[i]!
  return false
}

/** The `version` of a manifest's JSON text, or undefined. */
export function versionOf(text: unknown): string | undefined {
  if (typeof text !== 'string') return undefined
  try {
    const parsed: unknown = JSON.parse(text)
    const version = typeof parsed === 'object' && parsed !== null ? (parsed as { version?: unknown }).version : undefined
    return parseVersion(version) ? (version as string) : undefined
  } catch {
    return undefined
  }
}

/**
 * The marketplace an install came from, read off its folder: Claude Code keeps a marketplace
 * install at `…/plugins/cache/<marketplace>/<plugin>/<version>`. Anything else (a synced or
 * `--plugin-dir` copy) is undefined.
 */
export function marketplaceOf(root: string, plugin: string): string | undefined {
  const parts = root.split(/[\\/]+/).filter(Boolean)
  const at = parts.lastIndexOf('cache')
  const marketplace = at >= 0 ? parts[at + 1] : undefined
  return marketplace && parts[at + 2] === plugin && /^[A-Za-z0-9._-]+$/.test(marketplace) ? marketplace : undefined
}

export function updateNotice(plugin: string, installed: string, latest: string, marketplace: string | undefined): string {
  const how = marketplace
    ? `\`claude plugin marketplace update ${marketplace}\`, then \`claude plugin update ${plugin}@${marketplace}\``
    : `\`/plugin\`, then update ${plugin}`
  return (
    `The ${plugin} plugin is at ${installed} and ${latest} is released. At the end of your reply, tell the user in one ` +
    `line that they can update it with ${how}, and \`/reload-plugins\`. Say it once.`
  )
}

/**
 * A failure that is the network's, the user's sign-in, or a step atlan-troubleshoot and the skills
 * already handle (a retry, a conflict, an expired session): not something to send Atlan feedback on.
 */
export function isExpected(text: string): boolean {
  return (
    /\b(?:401|409|429|502|503|504)\b|-32003|rate.?limit|bad gateway|service unavailable|gateway time-?out|timed? ?out/i.test(text) ||
    /"retryable"\s*:\s*true|"may_have_run"\s*:\s*true|runner_below_tier|downstream_(?:unavailable|timeout|rate_limited)/.test(text) ||
    /authentication required|needs login|session not found|"partial"\s*:\s*true/i.test(text)
  )
}

/** `DO_NOT_TRACK` or `DISABLE_TELEMETRY` set to 1 or true turns the feedback offers off. */
export const isFlagOn = (value: string | undefined) => value === '1' || /^true$/i.test(value ?? '')
