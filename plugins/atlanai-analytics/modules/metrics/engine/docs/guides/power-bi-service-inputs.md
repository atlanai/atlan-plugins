# Power BI service inputs: scan results, paginated reports, scorecards

Besides a semantic model (a TMDL folder, a `.pbix`, a `.bim`), the agent reads three more
things the Power BI service holds. Each has its own reader, named in `bi_vendor`:

| `bi_vendor` | You supply | What it gives the run |
|---|---|---|
| `powerbi-scan` | a saved scan result (`.json`) | one dataset's tables, columns, DAX measures, Power Query lineage and relationships |
| `powerbi-paginated` | a paginated report (`.rdl`), or a folder of them | each SQL dataset as a statement, each table or chart as a question |
| `powerbi-goals` | a saved scorecard (`.json`) with the connected dataset's definitions beside it | each connected goal as a question over the measure behind it |

All three go through the `local-files` plug:

```yaml
settings:
  local-files:
    bi: exports/scan_result.json
    bi_vendor: powerbi-scan
    bi_model: Sales Model          # the one dataset to read, by id or name
```

`bi_model` is a setting of any BI input. It names the ONE model to read when a file holds
several. A reader whose file holds several refuses to choose for you: it stops and lists what
the file holds. A reader whose file holds one ignores it.

## A scan result (`powerbi-scan`)

**What to supply.** The body of the admin metadata-scanning API's result, saved as JSON:

1. `POST https://api.powerbi.com/v1.0/myorg/admin/workspaces/getInfo?datasetSchema=true&datasetExpressions=true`
   with the workspace ids in the body. This returns a scan id.
2. Poll `GET .../admin/workspaces/scanStatus/{scanId}` until it reports `Succeeded`.
3. Save the body of `GET .../admin/workspaces/scanResult/{scanId}` as a `.json` file.

`datasetSchema=true` gives the tables and columns; `datasetExpressions=true` gives the DAX and
Power Query. Without them a dataset is refused, and the message says which option to set.
`getArtifactUsers` is not needed: user lists are never read.

**The shape it reads** (Microsoft's documented `WorkspaceInfoResponse`):
`workspaces[].datasets[]` with `id`, `name`, `tables[].columns[]` (`name`, `dataType`,
`isHidden`, and when present `columnType`, `expression`, `sourceColumn`),
`tables[].measures[]` (`name`, `expression`, `formatString`, `isHidden`),
`tables[].source[].expression` (the Power Query source), `relationships[]` (`fromTable`,
`fromColumn`, `toTable`, `toColumn`) and `expressions[]` (shared queries and parameters). A
table whose source is a shared expression (`Source = Revenues`) takes that expression's
lineage.

**What you get, and what you don't.** A scan result has no report pages or visuals, so there
are no tiles. Each visible measure becomes one question, titled by the measure's own name.
**Widget titles and visual number formats are absent**; a measure's own `formatString` is kept
where the scan has one. The scan states no relationship cardinality, so it is recorded as
`unknown`. The run says all of this when it reads the file.

## A paginated report (`powerbi-paginated`)

**What to supply.** The report definition, a `.rdl` file: from Power BI Report Builder
(File, Save as), from SQL Server Reporting Services, or from the service with
`GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/reports/{reportId}/Export`. A folder
of `.rdl` files is read one report at a time and merged.

**The shape it reads.** The 2008, 2010 and 2016 report definition schemas, by element name:
`DataSources/DataSource` (`DataProvider`, `ConnectString`), `DataSets/DataSet` (`Query` with
`DataSourceName`, `CommandText`, `QueryParameters`; `Fields/Field` with `DataField`),
`ReportParameters/ReportParameter` (`DataType`, `DefaultValue`), and every `Tablix` and
`Chart` (`DataSetName`, `Filters`, the group expressions of their hierarchies, the cell or
series value expressions).

**What you get.**

- A SQL dataset becomes a statement, and its lineage is the table its SQL reads `FROM`. A query
  parameter with a static default (`@Season` defaulting to `AW25`) is fixed at that default,
  and the run says so. One with no static default is left as written, and the run warns that
  the statement will not run until it is fixed.
- Each table or chart becomes one question. Its aggregate expressions become measures in SQL:
  `=Sum(Fields!Amount.Value)` is `SUM(amount)` over the dataset's statement, `=CountRows()` is
  `COUNT(*)`. They are grouped by the fields of the group expressions and filtered by the
  data region's filters. A measure is named by the header text above its cell (for a chart,
  its series label), its number format comes from the cell's `Format`, and a chart's title is
  the question's title.
- An expression with no exact SQL form (`IIF`, `Lookup`, `RunningValue`, a scope argument such
  as `Sum(x, "DataSet1")`, a group filter, a computed group) is named in the run's notes and
  left out. It is never approximated.
- A dataset whose command is DAX or MDX against a Power BI model or Analysis Services is
  refused by name: its numbers are the model's, so supply the model instead (its TMDL folder,
  `.pbix`, `.bim` or a scan result). So are a shared dataset, a stored procedure and a command
  built from an expression. A report with no dataset left is refused.

The statement is in the report's own SQL dialect. Set the run's `dialect` to match it.

## A scorecard (`powerbi-goals`)

**What to supply.** A folder holding two things:

1. The scorecard, saved as JSON from
   `GET https://api.powerbi.com/v1.0/myorg/groups/{groupId}/scorecards({scorecardId})?$expand=goals`
   (a list from `GET .../groups/{groupId}/scorecards` is read too; name the scorecard in
   `bi_model`).
2. The definitions of the dataset the goals are connected to: a scan result that holds it (see
   above), its model as `<dataset id>.bim`, or its TMDL folder as the `powerbi-fabric` fetch
   writes it (`semanticModels/<workspace>/<dataset id>/`).

Point `bi` at the scorecard file or at the folder.

**The shape it reads.** Each goal's `name`, `valuesFormatString`, `goalValues` and its
`valueConnection` and `targetConnection`. A connection is null for a manual goal; for a
connected one it is `{"type", "datasetId", "reportUrl", "query"}`, where `query` is a JSON
string holding the report visual's semantic query: its `From` names the model table, its
`Select` the measure, its `Where` the visual data point's filters. Microsoft's REST reference
does not document `valueConnection`; this shape was checked against a live response. A
connection that names the measure directly (`measure` or `measureName`) is read too.

**What you get.** One question per connected goal: its measure is the one the connection
names, read from the dataset's definitions with its DAX, its title is the goal's name, its
filters are the data point's own, its number format is the goal's, and its target (a
connected target measure, or the latest typed-in target) is carried as context. A manual
goal produces nothing and is counted. A goal whose measure the dataset does not define, or
whose filter cannot be read, is named in the run's notes and left out.

One scorecard and one dataset per run. If the goals connect to several datasets, name the one
to read in `bi_model`; the others are counted.
