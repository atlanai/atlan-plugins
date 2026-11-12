// Written by tools/build.py from plugin.toml: edit that file.

/** This build's version, and where main's manifest says the latest released one. */
export const VERSION = "0.5.56"
export const LATEST_URL = "https://raw.githubusercontent.com/atlanai/atlan-plugins/main/plugins/atlanai/plugin.json"

import type { EngineInterface } from 'claude-code'

/** A fixed public GET, with no session data, request body or authorization header. */
export function fetchLatest($: Pick<EngineInterface, 'http'>) {
  return $.http.fetch("https://raw.githubusercontent.com/atlanai/atlan-plugins/main/plugins/atlanai/plugin.json")
}
