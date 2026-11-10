# Power BI capture procedure

Use the host's installed connector or documented computer-use APIs. Browser automation runs under the host's permission model. This package cannot assume a browser is installed, available or signed in.

Generic talk-to-data setup uses [shared setup](../../../analytics-build/references/setup.md) for metadata-first page selection,
authorized download reuse and private run locations. This procedure remains the explicit
dashboard-agent capture route. Neither route bypasses provider or host download permissions.

1. **Download first; do not tour every page.** Open only the supplied report, identify its actual title, then use **File → Download this file** if permitted. Reuse an already supplied matching export. Download once to the task's private working directory. Prefer a supplied PBIT or textual model export when available. A PBIX download can contain a binary model or only a live connection; it does not automatically provide readable DAX. Never bypass disabled downloads or permissions.

   **Import the downloaded file with `dashboard_import_file`.** Supply its absolute local path, the confirmed semantic-model reference, and the selected page context. `scope_complete` defaults to false; set it true only after verifying the actual filters, caller security and data cutoff. The report must be identified first. This tool decodes binary PBIX with PBIXRay, reads legacy or PBIR report layout, and imports every typed measure directly into the existing job. It returns the inventory card and a private `inventory_file` path. Read that file selectively for columns, relationships, security and Power Query source transformations when constructing SQL mappings. Do not retype the entire captured measure array through chat.

   PBIXRay 0.15.5 runs offline in a separate pinned runtime. Connections and DataMashup are excluded from its decoder input; row data and role membership are not exported. Embedded DAX/M is evidence, never executed. Credential-shaped source expressions are withheld. A missing runtime calls for the packaged `scripts/setup_pbixray.py` once, only when dependency installation is authorized. The MCP tool never installs dependencies. Standard package installation alone does not set up Python dependencies on a new machine.

   The optional standalone inspector is `~/.trident/runtimes/pbixray-0.15.5/bin/python <plugin-root>/scripts/inspect_powerbi.py <exact-file.pbix> --output <new-private-inventory.json>`. It also supports textual PBIT/model.bim. PBIP/TMDL projects require an existing compatible parser. Unsupported/corrupt binary models are explicit decode failures; a live-connection report without an embedded model requires its linked semantic model. Never turn a decode failure into an empty successful inventory.

   Use the exported page/visual inventory instead of clicking every page. After successful binary import, do not reread all measures in the browser. Visit only the selected page for current filters/date and independent reference answers. Use a bulk metadata query only when the file genuinely lacks necessary metadata or cannot be decoded. Do not start separate jobs or browser tours for every page unless the user requests separate page-specific validation. File defaults and current browser slicers can differ.

2. Record actual slicer selections, report/page/visual filters, bookmarks and cross-highlights that affect the reference visual. Inspect virtualized lists to their end. Preserve blank selections. Do not assume the URL carries every active filter. If a visual uses a different context, evaluate it under a separately captured scope.
3. Use **View semantic model**, then stay in **Viewing** mode. Use metadata through DAX query view where permitted. Suggested projection:

```dax
EVALUATE
SELECTCOLUMNS(INFO.VIEW.MEASURES(),
 "Source ID", [ID], "Table", [Table], "Name", [Name],
 "Datatype", [DataType], "Description", [Description], "Expression", [Expression])
```

The exact available metadata columns depend on the source; inspect errors and adjust only against source evidence. For additional context, inspect `INFO.VIEW.COLUMNS()` and a bounded projection of `INFO.VIEW.RELATIONSHIPS()`. Do not retain connection strings, partitions containing credentials or identifying customer rows in a distributable package. Capture measure-level formulas rather than reverse-engineering labels from screenshots.

4. Map source datatype to integer/decimal/string/boolean/date/timestamp. Record the model reference as runtime state. Measure metadata IDs are model-scoped; only claim rename stability when the upstream identity is stable. If using table/name, declare `name_fallback` and flag rename reconciliation.
5. A Monaco editor can append when filled. Select all, clear, insert, then read the entire query back. Run only the intended read-only DAX. Require successful execution and current results, not a stale grid. **Copy → Entire table** requires both menu actions; verify the clipboard header before reading it.
6. Capture business data-as-of separately from model refresh time. Retain timezone if relevant and caller security scope; never assert that all users see identical rows.
7. Reference extraction and answer execution are separate evidence. A reference may be a visible report card with its exact filter scope. Do not derive both gold and answer from the same generated query and describe them as independent.

For a report inaccessible to the caller, the next step is exact report/model access or a sanctioned metadata export. A screenshot can start a limited visual description, but cannot establish a complete metric definition or an executable agent.


For the metrics module, stage a downloaded report with the stable source key
`powerbi:<workspace-id>:<report-id>` from the selected report URL or metadata, using
`ENGINE stage-input --source <download> --destination <project>/sources/report --source-id <key>`.
Use the returned path for the reader. Browser-generated suffixes such as `(3)` are filenames,
never report identities. Do not strip such suffixes and assume two similarly named reports
are the same. Keep the display title separately in project state. No extra interview is needed.
