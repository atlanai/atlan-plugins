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
Given that address, the mod reads `/pane/v2` beside it for live detail and falls back to
`/pane` when an older Analytics skill serves no such route.

Every layout labels the same three rows: `Focus` (the business area), `Goal` (what to
accomplish) and `Now` (what the run is doing). `Now` carries a count where the run recorded
one, such as `Inspecting source 3 of 4` or `Checking definition 5 of 9`, changes tense when the
step ends, and adds `no change for 30s` when a working run stops reporting. Counts appear only
when they are above zero. Sources list what inspection counted in each file.

When the run waits on a choice, a `Needs you` row shows it with the pane's one primary
button. The button's label and the message it drafts come from a fixed list in the mod, keyed
by the step the feed names; the feed cannot supply button text. Choosing where to save,
restoring access, a blocked build and a run ready for questions also send one desktop
notification each, or a toast where notifications are off. A run ready for questions lists up
to three questions from its own saved queries as buttons.

Desktop renders a visual board with a three-step progress rail, count cards, a selected
metric card and review/local-state labels. Sources, SQL and check details expand on demand,
and SQL draws as highlighted code. The board uses the host's native Box, Text, Button and Code
components, inheriting Claude's typography and theme. It contains no custom SVG, hardcoded
palette or scaled text. Terminal panes use a compact dock of at most seven rows, with the same
rail on wide terminals. Where no pane is placed and the person has not closed it, one status
line under the prompt carries `Now`.

Every button except the disclosures fills the user's prompt and confirms that the request is
prepared; the user must submit it. A question button drafts `Answer this from the analytics
run:` followed by the saved question, single-line and at most 160 characters. Failed refreshes
label the retained evidence stale. Compact tool rows keep presentation JSON out of the normal
transcript. A Bash call gets a compact row, such as `Analytics · Read your sources`, only when
the whole command is one run of the packaged `project.py` with a known subcommand; a chained,
piped or substituted command keeps its full row, and ctrl+o shows every command in full.

It reads only the packaged preview's bounded loopback presentation through the host's HTTP
API, without credentials or request bodies. It reads no files, calls no Registry tools,
starts no process, changes no permissions and performs no analytics or publication work.
It registers no permission hook. Closing the pane prevents updates from reopening it.

To let a later session resume, the mod keeps one pointer in the host's plugin store: the
working folder, the business focus, the phase label and a time. It stores no metric, SQL or
source content, and removes the pointer when the run is ready. A session started in that
folder shows one toast, and `/analytics-view` drafts a resume request.

Host calls: `tool.register`, `command.register`, `ui.open`, `ui.close`, `ui.invalidate`,
`ui.panes`, `ui.resolve`, `ui.status`, `ui.notify`, `ui.toast`, `prompt.fill`, `http.fetch`,
`clock.every`, `clock.now`, `session.cwd`, `store.get`, `store.set` and `store.delete`.
Placement is read from `ui.panes` after `ui.open`. A presentation failure leaves the evidence
available in chat. Stop the owned preview with the host's task controls when done.
