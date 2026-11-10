# Dashboard through the metrics module

All Power BI and Tableau projects, including local-only setup and demos, use the maintained
metrics module. Existing saved dashboard jobs stay readable as source evidence; new generation
or revisions use a new main-engine run. Keep identification, evidence and progress in
native chat. No additional MCP server is installed.

1. **Identify and capture.** Reuse the supplied original file or fetch once through the
   existing authorized browser/BI connector. Keep its source URL, title and identity. The
   existing dashboard helper may identify or inventory a Power BI download, and retain capture evidence. Its retired translator and card exporter are unavailable. Read
   [Power BI capture](dashboard/powerbi-capture.md) when fetching Power BI. For Tableau,
   obtain the original workbook and its referenced datasource definitions. A missing file
   is a named gap, not a reason to infer definitions from chart values.
2. **Reuse the interview.** Use the already selected workspace, purpose, table scope,
   warehouse connection and approval mode. Propose missing physical mappings from actual
   lineage. Ask only for unresolved business choices. Inherit auto approval as the project
   conventions describe; never request a second setup interview.
3. **Run generation.** Follow [the metrics module](metrics-engine.md) with BI inputs and any
   selected history inputs, keeping original filters, security scope and source refresh
   evidence. The original reader/compiler produces SQL, and the assistant answers the
   original reviewed gates. Unsupported constructs remain named gaps. The module exports
   the existing context files; it makes no Registry calls.
4. **Check dashboard parity independently.** When exact source values are reachable, compare
   them against the generated SQL for the same filter, security and snapshot scope. Use
   `ENGINE parity-plan` and the private evidence contract in the metrics-module reference,
   then `ENGINE export --parity` to apply the existing result comparator. Store the query,
   both results and scope under the private engine run's `parity/`, separate from context.
   Missing references or different snapshots are unmeasured, not failed accuracy. Show
   passed/compared and unmeasured counts. The engine's SQL witness is a separate check.
   A genuine mismatch must be corrected or the metric held before saving; never relabel
   it as a snapshot gap. Do not feed reference result values into authored definitions.
5. **Publish.** Continue at metrics workflow section 4 and its existing publication contract.
   Review usable metrics, held items and evidence limitations. Preserve the existing
   create/revise/reuse behavior and release readbacks. Local export is not publication;
   an unreleased head is not released context. Record parity limitations with the published
   definitions through the adapter's extension fields, without rewriting the engine output.
6. **Build and evaluate.** Continue with agent setup and independent eval-set creation. Gold
   must be independently trusted, not the converter's SQL reused as its own answer key.
   Keep dashboard parity, engine verification and held-out answering accuracy separate.
   Missing independent gold or an isolated runner means Not run; never fabricate a score.
7. **Ask.** After actual save/release, use the existing ask skill with released definitions
   and fresh warehouse reads. Offer up to three questions supported by the usable context.
   A local-only project stays explicitly provisional. Unknown parity does not prevent a
   fresh warehouse answer; preserve its limitation and source cutoff.

Metrics-only requests stop after metric publication. They do not create an agent or eval set.
