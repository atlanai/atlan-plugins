/** The Atlan connection as the last check or call found it. */
export type AtlanHealth = {
  state: 'unknown' | 'ready' | 'sign-in' | 'missing' | 'unreachable' | 'error'
  /** The server name the check reached, as /mcp lists it. */
  server: string
  /** A short reason, never a token or a response body. */
  detail: string
  /** Milliseconds since the epoch. */
  checkedAt: number
}

declare module 'claude-code' {
  interface PluginState {
    'atlanai-mods': {
      /** The gateway's audit session for this Claude Code session. */
      session: string
      health: AtlanHealth
    }
  }
}
