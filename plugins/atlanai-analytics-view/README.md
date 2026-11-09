# Analytics View

Show Analytics source understanding, business intent, one metric and saved progress beside
the Claude Code conversation. Works in supported CLI and Desktop Code sessions.

Install alongside Analytics: `claude plugin install atlanai-analytics-view@atlan`.
The Analytics skill supplies a bounded presentation payload to the `show` tool after
inspection and at meaningful phase changes. `/analytics-view` reopens a dismissed pane.
Narrow or headless sessions retain the same evidence in chat.

Desktop Code uses a side panel. The CLI uses a sidebar in a wide fullscreen terminal and a
framed dock above the prompt in the classic renderer. For a single fullscreen session use
`CLAUDE_CODE_NO_FLICKER=1 claude`; `/tui fullscreen` also works but saves the host preference.
These are host layout controls, not plugin settings.

The mod registers one presentation tool and one command. It renders only the supplied text,
keeps it in session memory, and uses native buttons to fill the user's prompt. The user must
submit that prompt. Inspect source and checks toggles local detail visibility.

It reads no files, calls no network or Registry tools, starts no process, changes no
permissions, and performs no analytics or publication work. It registers no permission
hook. Closing the pane prevents subsequent updates from reopening it automatically.

Host calls: `tool.register`, `command.register`, `ui.open`, `ui.close`, `ui.invalidate`,
`ui.resolve` and `prompt.fill`. A presentation failure leaves the evidence available in chat.
