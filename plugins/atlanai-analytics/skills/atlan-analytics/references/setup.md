# Prepare existing context for questions

Use seven stages: **Inspect, Scope, Build, Check, Preview, Publish, Ready to ask**.
The result is released metrics and saved SQL in Atlan, used by this chat through MCP.
An input folder is ordinary customer context. Never depend on a particular repository,
sample schema or database name. Explicit agent and evaluation modes remain separate.

`PROJECT` means `python3 "<skill-dir>/scripts/project.py" --run-dir "<run-dir>"`.
Python 3.10+ with its standard library is enough: CSV data is prepared into SQLite, which
needs no package install. An existing `.duckdb` file still needs a runtime with DuckDB.
The [local procedures](project.md) describe binding, preparation and complete release readbacks.
Missing Registry access follows [connection recovery](auth-recovery.md): inspect the current
host, start the host's sign-in when required, watch its native task and resume after a
successful Registry read. Continue independent local work while waiting; do not simply ask
for "continue".

## Read one stage file at a time

Open only the file for the stage you are entering. A resume starts at the saved stage, and a
run whose candidates were never shown in chat starts at Preview.

| Entering | Read |
| --- | --- |
| Inspect or Scope | [inspect and scope](setup/inspect-scope.md) |
| Build or Check | [build and check](setup/build-check.md); new metrics also follow [the metrics module](metrics-engine.md), except setups whose inputs are all local files |
| Preview, Publish or Ready to ask | [preview and publish](setup/preview-publish.md) |
| A long helper run, a heartbeat or a panel | [progress and presentation](setup/progress.md) |

## Preview first, in this reply

**Visible preview is a required conversation step.** Before a destination question or
publication/reuse, show actual candidates in an assistant message. Tool output, counts and
an HTML link alone do not satisfy this step. This applies on resume and when everything
already exists in Atlan. It is a presentation checkpoint, not another approval prompt.

- When the conversation already holds the candidates (a pasted checkpoint, an earlier helper
  result, a linked file you can read), render them now, in this same reply, before any
  question: every supplied metric with its name, meaning, calculation, source and check
  status, and each saved query's SQL verbatim, mandatory filters included. Do not ask whether
  to show them and do not re-run anything to show them.
- If the previous turn only linked a file, resume at Preview and render its content before
  announcing Ready. Do not label the run complete while that visible step is still missing.
- Repeat the table and SQL in the final answer of the run, whatever its outcome: hosts
  collapse commentary, so the final answer must stand alone.

On resume, reuse a readable run or candidate output already supplied in the conversation.
If neither is available, ask only for the existing run directory. Do not search unrelated
folders or home caches, request the original source again, or restart extraction to satisfy
a preview choice. With no shell, read existing candidate/preview files through the host's
file tool and show them directly. If the path is missing, name that one missing input.

## Reply shape

The preview table and saved SQL are the only part of a reply allowed past the 80-word limit.
Around them: one line for what this turn did, one for the next step or the single question,
then the status line. No headings, no stage-by-stage narration, no restated rules, no list of what was
not done. When the candidates were reconciled, say in one sentence per changed count why
(reused, kept local for validation, held), not the whole reconciliation.
