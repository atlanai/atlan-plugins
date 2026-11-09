# Analytics View

Show Analytics source understanding, business intent, one metric and saved progress beside
the Claude Code conversation. Works in supported CLI and Desktop Code sessions.

Install alongside Analytics: `claude plugin install atlanai-analytics-view@atlan`.
The Analytics skill starts its packaged local preview after inspection and binds the
`follow` tool to its exact `pane_url`. Saved progress refreshes every 2.5 seconds without
another model call. `/analytics-view` reopens a dismissed pane.
Narrow or headless sessions retain the same evidence in chat.

Desktop Code uses a side panel. The CLI uses a sidebar in a wide fullscreen terminal and a
framed dock above the prompt in the classic renderer. For a single fullscreen session use
`CLAUDE_CODE_NO_FLICKER=1 claude`; `/tui fullscreen` also works but saves the host preference.
These are host layout controls, not plugin settings.

The mod registers two presentation tools and one command. `follow` accepts only
`http://127.0.0.1:<port>/pane`; `show` retains compatibility with bounded static snapshots.
Desktop renders a visual board with a three-step progress rail, count cards, a selected
metric card and review/local-state badges. Sources, SQL and check details expand on demand.
The board is escaped, script-free SVG with accessible alternative text and light/dark colors.
Terminal panes retain a compact text layout. It keeps presentation in memory and uses native buttons to fill the user's
prompt. It confirms when the request is prepared; the user must submit it. Inspect source
and checks toggles local detail visibility. Failed refreshes label the retained evidence
stale. Compact tool rows keep presentation JSON out of the normal transcript.

It reads only the packaged preview's bounded loopback presentation through the host's HTTP
API, without credentials or request bodies. It reads no files, calls no Registry tools,
starts no process, changes no permissions and performs no analytics or publication work.
It registers no permission hook. Closing the pane prevents updates from reopening it.

Host calls: `tool.register`, `command.register`, `ui.open`, `ui.close`, `ui.invalidate`,
`ui.resolve`, `prompt.fill`, `http.fetch` and `clock.every`. A presentation failure leaves
the evidence available in chat. Stop the owned preview with the host's task controls when done.
