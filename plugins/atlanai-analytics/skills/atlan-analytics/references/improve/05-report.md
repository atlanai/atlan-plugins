# Report and hand back

When the loop ends, leave the registry and the subagent at the round that won on **test**. If
a later round lost, apply its receipt in reverse.

Rewrite `H/narrative.md` one last time: the status table on top, then four short parts.

1. **Headline.** The test pass rate at baseline, then at the winner, each with its band, and
   the delta. If the bands overlap, say plainly that the change didn't clear noise and
   recommend not relying on it.
2. **Changes now in place.** List each one with its one-sentence reason from `change.md` and
   the registry ids and versions from `receipt.json`. Tag each:
   - `[REQUIRED]`: it fixes something broken, such as a wrong definition or a failing query;
   - `[TUNE]`: a judgement call the user could decline.
3. **Why trust this.**
   - The analyst read train only.
   - The grader is deterministic, and its digest was approved.
   - Harness errors were excluded, listed by case.
   - Include two before/after pairs from **train** cases showing the behaviour that changed.
4. **What's next.** Levers not tried yet, and concepts still uncovered. If definitions changed,
   suggest having a data owner review and certify the new metric versions in the registry. The
   agent never marks anything certified.

In chat, give the headline line, the path to `narrative.md`, and a pointer to the agent's
Evals tab in the registry. The baseline and every round appear there as linked experiments.
