# Open the Analytics view

Read after inspection on fresh setup, before asking the business-focus question, and on
resume. The visual experience opens automatically as part of setup. A helper output or file
link does not display a view. Use the actual host tool and check its result. Keep the domain
and purpose questions native, one at a time, alongside the view. Never silently replace a
failed render with a claim that the UI is open.

## Prepare the same evidence for every host

Save a run-local `overview.json` with `summary` (the short, observed source explanation) and
`opportunities` (up to three possible analyses). No invented findings or source relationships.
Use `PROJECT experience --format panel --overview-file <run-dir>/overview.json`.
This records presentation prose separately from checks and binds it to the surveyed sources.
Later updates omit `--overview-file`; source changes invalidate the earlier prose.
During engine work pass `--engine-dir <within-run-engine>` and retain `engine_run` in run.json.

The result contains `snapshot` (an absolute local HTML path) and `pane` (bounded native text).
`displayed: false` is intentional: the host must perform the next step. Save progress at the
first metric, every meaningful check/phase transition, a hold, a choice change and the final handoff.
Keep the same surface and metric selection. Never reopen a view the person dismissed unless
they ask. No view action publishes, executes SQL, or changes an engine checkpoint.

## Claude Code CLI and Desktop Code: native side pane

When the Analytics View companion's `follow` tool is available, start the packaged
`experience_view.py --run-dir <run-dir>` using
the host's managed preview process or long-running terminal tool. Follow the process rules
below: retain its task handle, use the approved interpreter and do not daemonize it. Omit
both connection flags before sign-in. Once identified, add `--host claude_code --server
<observed-server>` together on a later launch if needed; never supply only one. Pass its exact
`pane_url` to `follow` as `url`. Never point it at another local service. The pane reads the
saved run every 2.5 seconds, including intent answers, engine checkpoints, checks and recovery.
Keep `engine_run` in run.json current; no repeated `show` calls or generated payloads are needed.
Desktop Code places it beside the transcript. The CLI uses a sidebar in wide fullscreen
terminals and a framed dock above the prompt otherwise. Check the tool result for actual
placement. If only the older `show` tool exists, pass the prepared `pane` as `view`, label it
a saved snapshot and refresh it explicitly at meaningful transitions.
Installing Analytics in Claude Code automatically includes its native view. Do not ask the
person to find or install a separate view plugin. If an older installation lacks the tools,
continue with the fallback below and explain once that updating Analytics and reloading
plugins restores the included view. Do not change installations unless requested.
`/analytics-view` reopens the last pane. Its buttons use
native prompt filling: the person submits the filled text to request pause, local mode or
details. The pane confirms that a request was prepared; it does not claim to have applied it.
Inspect source and checks expands evidence in the pane itself.

A narrow terminal or headless session may not place a pane. Give the concise preview in chat
and name the limitation once. Do not change permissions, terminal settings or policy to force
placement. With no mod in Desktop Code, use the local Browser route below. With no mod in a
terminal, offer the local browser view and retain text. Do not claim a CLI side pane exists
because HTML was written or opened in a browser.

For someone who asks for a CLI sidebar, explain that `/tui fullscreen` switches the host's
renderer and saves that preference. They can start a single session with
`CLAUDE_CODE_NO_FLICKER=1 claude` instead. Do not change the saved preference automatically.

## Codex: open the live local Browser panel

Start the packaged `experience_view.py --run-dir <run-dir> --host codex --server <observed-server>`
with the host's normal long-running terminal tool. Use the same approved interpreter as
PROJECT. Keep its returned process session so it can be stopped; do not daemonize it. It
binds only 127.0.0.1, selects an unused port and prints its exact `url`. Omit both host/server
if the selected host connection has not yet been identified. It serves only the bounded
presentation, never the source directory, raw files or credentials. It offers no write route.

Call the available `open_in_codex` tool with `target: {type: "browser", url: <returned-url>}`.
If it returns queued, report queued, not visible. Where available, use the in-app browser
tools to inspect the actual page. A real rendered page must contain What we found and the
actual selected metric once available. Keep the panel open; it follows saved evidence every
2.5 seconds and retains disclosure/filter state. Do not repeatedly open tabs on every update.
If the person closes the panel, keep progressing in chat until they ask to reopen it.

## Claude Desktop Browser fallback

Discover the available preview tools. `mcp__Claude_Browser__preview_start` accepts `name` for
a server configuration in `.claude/launch.json`, or `url` for an already-running preview.
When starting a server here, use this host tool, not a background Bash process. Add one
configuration named `analytics-view` with `runtimeExecutable` equal to the approved Python,
`runtimeArgs` equal to the packaged experience_view.py path, `--run-dir`, the exact run path,
and `--port`, a free local port; `port` is that same number. Preserve other configurations.
Use absolute paths and JSON arrays, never a shell command string. An existing port in use
needs a different free port, not process termination. Call preview_start with that name.
Use the host's page text or screenshot tool to verify the view, and retain its serverId.
Alternatively, `SendUserFile` with `files: [snapshot]`, `status: "normal"`, `display: "render"`
shows the local snapshot inline if available; it is static and must not be called live.

The local Browser page uses system colors, responsive layout, real SQL/source disclosures
and filters. Copy-request buttons explicitly copy text to submit in chat; they never claim
to pause or publish themselves. When refresh fails, it labels the retained evidence stale.
Stop an owned preview process through its host task controls when it is no longer needed.
Never publish this page to a cloud Artifact or external site as an unannounced fallback.
