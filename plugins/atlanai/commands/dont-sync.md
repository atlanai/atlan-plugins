---
description: Keep this session from ever syncing to any Atlan organization from this computer, and delete its content where it was already uploaded.
disable-model-invocation: true
---

This session's id: `${CLAUDE_SESSION_ID}`.

Exclude this one session, through Atlan's CLI, and show what it did. Say only what a command
printed; never guess whether anything was uploaded or deleted. Use a shell; where there is none
(Claude chat, Cowork), reply "Excluding a session needs Atlan's CLI on your computer, and this
session cannot reach it." and stop.

1. Find the CLI: `command -v aireg || command -v brain || ls ~/.aireg/bin/aireg`. Its installer
   may name it `brain`; the two take the same arguments. Missing: reply "Atlan's CLI (aireg) is
   not on this computer, so nothing from it syncs to Atlan and there is nothing to exclude." and
   stop.
2. The id above is this session. If it still reads as a placeholder (a `$` and braces), reply
   "This tool did not say which session this is, so nothing was excluded. Find it with
   `aireg trace list`, then run `aireg trace exclude <session id>`." and stop. Never pick a
   session by date or by guess.
3. Run exactly `aireg trace exclude <this session's id>`, with no other argument. Run nothing
   else: no `trace export`, no `trace include`, no `job` command.
4. Reply by what it printed:
   - It printed "This session won't sync to any organization from this computer." (exit 0 or 1):
     show everything it printed, verbatim, in a `text` block, and nothing else but one last line:
     "To sync it again: `aireg trace include <session id>`."
   - It failed naming `Unknown trace action "exclude"`: reply "This computer's aireg is too old
     to exclude a session, so nothing was excluded and background sync still includes this
     session. Update the Atlan desktop app, then run `/atlanai:dont-sync` again." and stop.
   - Any other failure: run `aireg trace excluded`, then reply with the error in its own words,
     and say whether this session's id is in that list (excluded) or not (not excluded). If that
     second command fails too, say it cannot tell whether this session was excluded.
