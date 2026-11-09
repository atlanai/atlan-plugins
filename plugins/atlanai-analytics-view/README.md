# Analytics View

Show Analytics source understanding, business intent, one metric and saved progress beside
the Claude Code conversation. Works in supported CLI and Desktop Code sessions.

Included automatically when installing Analytics. Use one command:
`claude plugin install atlanai-analytics --marketplace atlanai/atlan-plugins`.
This package is the Claude-specific presentation component, not a separate setup step.
The Analytics skill starts its packaged local preview after inspection and binds the
`follow` tool to its exact `pane_url`. Saved progress refreshes every 2.5 seconds without
another model call. `/analytics-view` reopens a dismissed pane.
Narrow or headless sessions retain the same evidence in chat.
Restart a running CLI session after updating the companion. If automatic placement is
declined in a narrow terminal, `/analytics-view` explicitly opens its compact dock.

Desktop Code uses a side panel. The CLI uses a sidebar in a wide fullscreen terminal and a
framed dock above the prompt in the classic renderer. For a single fullscreen session use
`CLAUDE_CODE_NO_FLICKER=1 claude`; `/tui fullscreen` also works but saves the host preference.
These are host layout controls, not plugin settings.

The mod registers two presentation tools and one command. `follow` accepts only
`http://127.0.0.1:<port>/pane`; `show` retains compatibility with bounded static snapshots.
Desktop renders a visual board with a three-step progress rail, count cards, a selected
metric card and review/local-state labels. Sources, SQL and check details expand on demand.
The board uses the host's native Box, Text and Button components, inheriting Claude's
typography and theme. It contains no custom SVG, hardcoded palette or scaled text.
Terminal panes retain a compact text layout. It keeps presentation in memory and uses native buttons to fill the user's
prompt. It confirms when the request is prepared; the user must submit it. Inspect source
and checks toggles local detail visibility. Failed refreshes label the retained evidence
stale. Compact tool rows keep presentation JSON out of the normal transcript.

It reads only the packaged preview's bounded loopback presentation through the host's HTTP
API, without credentials or request bodies. It reads no files, calls no Registry tools,
starts no process, changes no permissions and performs no analytics or publication work.
It registers no permission hook. Closing the pane prevents updates from reopening it.

Host calls: `tool.register`, `command.register`, `ui.open`, `ui.close`, `ui.invalidate`,
`ui.panes`, `ui.resolve`, `prompt.fill`, `http.fetch` and `clock.every`. Placement is read from
`ui.panes`; `ui.open` has no placement return value. A presentation failure leaves
the evidence available in chat. Stop the owned preview with the host's task controls when done.
