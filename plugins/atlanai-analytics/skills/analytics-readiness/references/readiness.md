# Readiness check

Find out whether this person is ready to build an agent with Atlan Analytics on one use case of
theirs: a coding agent, Atlan Trident, one way to run SQL on their warehouse as themselves, and a
BI dashboard to start from. It ends with a report at `.atlan-analytics/readiness/report.md`: a
verdict, and each step that would speed the build up, with who takes it and the command. About ten
minutes. It needs only this skill's folder: no other skill, no setup and no build helper.

`READY` means `python3 "<skill-dir>/scripts/readiness.py" --workspace "<working-directory>"`, where
`<skill-dir>` is this skill's folder (`${CLAUDE_PLUGIN_ROOT}/skills/analytics-readiness` in Claude
Code). Any Python 3 runs it (`py -3` on Windows). Pass separate argv entries; each command prints
one line of JSON. Never open its state file.

Atlan calls in search mode (the default) go through the runner for what they do,
`mcp__call_read_tool` for a read, with `session_id` and a first-person `rationale` beside `name`
and `arguments`; the first call opens the session by sending `objective`. In full mode, open it
with `mcp__initialise` and call the tool directly. Replies lead with the result, in four sentences
or fewer and under 80 words (a table or a list the person asked for aside). Check each: no
greeting, apology or closing offer; counts rather than lists; one next step, not a menu; nothing
about how tools work or which files you read.

## Rules

- **Read-only, and nothing installed or changed.** You run the script, one Atlan read and one check
  per warehouse route, each check after a yes. Installing a CLI, signing in on its own, adding a
  connection or a grant is the person's or their admin's to do: the report gives its exact command,
  and you never run it here, even when asked mid-check.
- **Names only.** Never open a CLI's or an agent's configuration file, `.env`, a key or a token
  file. The script lists names and replaces those that look like account identifiers with labels
  (`snowflake connection 2`). Use the labels in the chat and in every `record`, and never write a
  name that reads as an account, workspace or organization id, even one the script kept.
- **A browser sign-in happens inside its check.** Say a tab may open in the question, and run the
  check with a long timeout (300 seconds, or your tool's maximum). In Codex's sandbox, ask to run
  that one command outside the sandbox; when that is refused, the script records it.
- **Collect everything.** A failed check is a finding: record it and go on.
- **Every yes is a choice, one at a time.** Claude Code: `AskUserQuestion`, the recommended option
  first. Codex: `request_user_input` where the mode allows it, otherwise the command approval
  prompt. Cursor, Cortex Code and others: one line ending in numbered options. Only the use case is
  an open question.
- **Nobody there** (a scripted run): ask nothing, treat every yes as no, record answers as
  `unanswered`, and still write the report.
- **No shell** (a chat app): skip the script, run the Atlan and warehouse checks through tools, and
  show the report's sections in the chat.

Show this checklist in the host's task list, or in the first reply, and tick it as you go, with one
status line per step (`<n>/6 <step> · <result>`): use case; 1 coding agent; 2 Atlan Trident;
3 warehouse; 4 BI and starting dashboard; 5 who you are; 6 report.

## Use case

In two sentences: the build starts from one question their team asks of its data again and again,
and every check after this is so they can start on it. Then ask: "What would you like an agent to
answer? A sentence or two: the question your team keeps asking, who asks it, and where the answer
lives today." "Not sure yet" is fine. `READY record use-case --status answered --set text="<their
words>"`.

## 1. Coding agent

`READY probe --fresh` returns: the coding agents installed and their versions; each agent's MCP
servers, warehouse, BI and Atlan ones by name and the rest as a count; the warehouse CLIs with
their connection or profile labels; BI apps; dashboard files in this folder, each opened read-only
(pages, measures, the warehouse it reads, or data inside the file); dashboard file counts in
Desktop, Documents and Downloads; the OS and the Python Atlan Analytics will use; and where the
person's name and email can come from. Record the agent you run in, which you know without
guessing: `READY record harness --status verified --set name="<agent>" --set version="<version>"`,
adding `--set task_list=no` in Claude Code when you have no task list tool.

## 2. Atlan Trident

One `registry__list_workspaces` read through `mcp__call_read_tool`, which opens the session. Record
`READY record atlan --status verified --set workspaces=<count>`; a refused or signed-out read is
`failed --set error="<first line>"`, and no Atlan tools in this agent is `none`. Carry on either
way.

## 3. Warehouse

Follow [warehouse checks](warehouse-checks.md): your own tools first (behind Atlan, or
connected here), then the CLIs, in its order. Ask once per route: "Run the check for `<route>`? It
reads who you are signed in as and whether your team's past queries can be read. If its sign-in
has expired, a browser tab will open." Options: "Run it (Recommended)", "Skip". That one yes covers
both statements; a CLI check runs and records both, and through a tool you run the past-queries
statement from the same file and record it. With
no route at all, ask "Which warehouse does your team use?" (Snowflake, Databricks, BigQuery,
Redshift; Other) and record `READY record warehouse --status none --set kind=<kind>`: the report
then says how to connect it.

## 4. BI and starting dashboard

Ask, one at a time:

1. "Which BI tool does your team use?" Options: Tableau, Power BI, Looker, Sigma, with any found on
   this machine moved first; add "Other" where the host does not.
2. "Which one dashboard would you start from?"
3. "Do you know which tables in your warehouse hold the data for it, or would answer your use
   case?" Options: "Yes, I can name them", "Roughly", "Not sure". On yes or roughly, take the names
   in chat; on not sure, say "No worries, we'll work with you 1:1 if these are needed."

`READY record bi --status answered --set tool="<tool>" --set dashboard="<name>" --set
tables_known=<yes|roughly|no> --set tables="<names as given>"`. When the dashboard's file is in
this folder, the probe opened it: record `--status verified --set file="<file>" --set pages=<n>`,
with `--set measures=<n>` when known and `--set inside_file=yes` when its data is inside the file.
Never ask for exports, screenshots or uploads.

## 5. Who you are

Take the name and work email from the first source that has them: the user the warehouse check
returned, then the probe's `identity` (git config, the OS account, GitHub). Prefer a company
address over a personal or `noreply` one. Confirm in one line: "I have you as <name>, <email>."
Options: "Correct (Recommended)", "Change it". Ask only for what no source gave. `READY record
participant --status answered --set name="<name>" --set email=<email> --set
source=<warehouse|git|os|github|asked>`.

## 6. Report

`READY report` writes the report and prints the verdict (`ready` or `needs_attention`, said as
"Ready" or "Needs attention"), why, the first suggestion and the prompt that starts the build. Show
the summary table, the verdict and the first suggestion with its command, then the report's path.
It stays on this machine: never send it or its contents anywhere. The person shares the file with
whoever they choose.

Close with `6/6 report · <verdict> · .atlan-analytics/readiness/report.md` and the report's start
prompt as the one next step. A build started in this folder reads these answers and does not ask
them again: say so in one line. A suggestion they want done now (installing the Snowflake CLI,
say) is theirs to run: show its command, and run the readiness check again afterwards.
