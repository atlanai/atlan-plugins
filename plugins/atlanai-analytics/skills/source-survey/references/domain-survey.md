# Domain survey: what already exists about a subject

Read when the caller passes a subject and no source, such as "sales pipeline" or a question
`analytics-ask` could not answer. The survey finds what is already defined, compares it, and
says what is missing. It asks the person nothing and changes nothing.

## 1. Words to search with

Take the subject's own words and their plain synonyms: for sales pipeline, also opportunity,
deal, win rate, stage. Three to six words. Do not widen to neighbouring subjects.

## 2. Look in every place, together

| Place | How | Keep |
| --- | --- | --- |
| Released metrics in Atlan | `extension__metric__search_metrics` with the words as `query`, `semantic: true`, `include_definition: true`, across workspaces, as `analytics-ask` step 2 does | Name, workspace, formula, filters, owner |
| Saved queries in Atlan | `extension__metric__search_queries` with the same words | Name, workspace, what it returns |
| Team rules in Atlan | `registry__search_artifacts` with the words, kinds `skill` and `knowledge` | The rule in one line, and where it lives |
| Certified queries | The host's certified-queries skill, where listed. Read `call` before trusting a title | Title, the rule the SQL applies |
| Reports and models | [Setup survey](setup-survey.md) step 1's file and connector rows, kept to names that match | Pages and measures that match |
| Warehouse tables | One metadata statement for table names that match, through the recorded query tool | At most ten, ranked by how many of the released metrics' columns they hold |

A search always returns something. Keep a hit only when its name or formula measures the
subject; sharing one word is not a match. Reading a definition never runs it.

## 3. Compare what was found

For each number defined in more than one place, compare formula, population, filters and time
basis. A difference is real when the two rules can return different values, not when they are
worded differently. For each real difference record:

- the number, and what each place says, in plain words;
- the size of the difference when one read-only statement shows it (run both rules on the
  same window), otherwise "not measured";
- which place is the released one. A released Atlan metric is the reference; a team rule in
  Atlan outranks a query that ignores it.

Never edit, re-save or withdraw another owner's metric, rule or certified query. The survey
reports; a fix is a proposal to its owner, through `metrics-generation` `propose`.

## 4. Return three things

1. **What exists**, one row per place with counts and names.
2. **Where they disagree**, one row per real difference. None found is a result too: say so.
3. **The inventory**, each name with a status:

| Status | Means |
| --- | --- |
| Reuse | A released metric covers it. It is used as it is and never rebuilt |
| New | Nothing released covers it, and query history or a report shows the team asks for it. State the evidence, such as the count of past queries |
| Flag | Something in use contradicts the released rule. It is left alone and reported to its owner |

New names come from the sources in the usual order: reports and models, then query history,
then table definitions. The sources to run `inventory` on are the ones the new names need.

When nothing exists anywhere and no table matches, `readiness` is `none`: say which words
were searched and ask the caller for a source.
