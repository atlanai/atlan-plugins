# Claude and Codex eval runners

Use the registered Claude subagent when its read-only tool boundary is available. Otherwise
use the bundled question-only runner in Claude Code or Codex. Both produce a real transcript
and feed the same deterministic Analytics grader and Registry publisher.

## Prepare once

Record the runner in project state and the experiment description. Read each intended metric
with `channel:latest` or its approved release ID. Build a private context JSON array containing
only `{id, release_id, name, description, definition}`. Include no eval question, gold query,
gold result, expected answer or unapproved draft. Record the snapshot hash alongside the run.
The snapshot is fixed before evaluation; context cannot be selected using a case's gold.

The runner is a fresh CLI process in an empty temporary directory. Claude uses restricted
mode with no tools or MCP servers. Codex disables shell, browser, computer use, apps, plugins,
images, memories and subagents, loads no user config, and retains managed policy. It never
copies auth files or changes the user's installed configuration. The transcript audit refuses
any tool call. An unsupported host version, expired sign-in or policy refusal is a harness
error; never relax these controls to get a score.

## Run one case

Save only the current question in a UTF-8 file, then run:

```sh
python3 "<skill-dir>/scripts/analytics_case.py" --host codex --context <released-context.json> --question <question.txt> --out <new-case-directory> --instructions <approved-agent-instructions.txt>
```

Use `--host claude` in Claude Code. Choose a new output directory for each attempt; a failed
run's transcript stays available. Authentication uses the installed CLI's normal store.

The runner returns paths to `answer.json` and `transcript.jsonl`. The answer contains `sql`,
`parameters` (name/value pairs), `answer` and `used_context_ids`. Its prose proposes a query;
it cannot claim a result until warehouse execution succeeds. Check parameter uniqueness and
bind them separately. Validate SQL as one read-only SELECT/WITH, constrain it to the declared
scope and relations, and run it through the approved warehouse adapter. Never interpolate
user text into SQL or execute a proposed shell command. Save `{columns, rows, error}`.

Use the existing `EVAL record` with those answer, transcript, actual and independent gold
result files. Use `--class harness_error --reason ...` for failed host runs. Missing transcripts
or unexecuted SQL do not qualify as passing cases. A runner that requested tools is refused.
`analytics_transcript.py` reads both Claude JSONL and Codex events; unknown executable events
are contamination. Raw transcripts remain private audit evidence.

## Scope of the score

Report the host and `question-only released-context` runner beside the rate. It measures
SQL answering given the released definitions; it does not measure Registry search/retrieval.
Compare like-for-like runner/context versions. Use a separate retrieval eval for discovery.
The scoring harness digest includes the runner and transcript adapter so changes invalidate
an earlier approval. Continue through `EVAL summary` and the normal publication flow.

For ordinary user questions the host retrieves current releases and runs the warehouse query
in native chat. Eval gold never enters that conversation.
