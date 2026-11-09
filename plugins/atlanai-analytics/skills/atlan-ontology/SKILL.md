---
name: atlan-ontology
description: "Use the business ontology in Atlan: explain a business object and its joins, say which policy applied on a date, or draft an ontology. Use when a question names business entities, terms or policies. Not for metric numbers (atlan-analytics-ask)."
---

# Business ontology

Read [shared conventions](../atlan-analytics/references/conventions.md) first; its
**Registry calls**, **Safety** and **Status line** sections apply.
This skill reads ontology releases and prepares drafts. It never saves a definition, creates a
binding or publishes a release: Atlan reserves those for a person in Ontology Studio.

An ontology names the business: object types (Customer, Order), their properties, the links
between them, actions, and SKOS terminology. A **binding** maps those logical names to physical
tables and columns. **Governed records** are policies, controls and procedures typed by the
ontology, each valid over a business-time interval.

## 0. Pick the mode

| The request | Mode |
| --- | --- |
| What is X, how does X relate to Y, which table holds X, which columns join them | **context** (section 2) |
| Which policy, control or rule applied on a date, or applies now | **records** (section 3) |
| Model this domain, turn these tables or this OWL/SKOS file into an ontology | **draft** (section 4) |

An explicit first argument (`context`, `records`, `draft`) wins. Otherwise pick from the
request, and ask once only when two modes fit equally well.

## 1. Preflight

Records mode runs steps 1 and 3 only, then section 3: `extension__ontology__get_records` takes
no release id (each record it returns names the release it came from), and an uninstalled app
shows as that call being refused as an unknown tool (stop with BLOCKED as step 2 says). Context and draft
modes run every step.

1. Open a registry session as the conventions describe, with an `objective` naming the domain.
2. Confirm the Ontology app is installed: `mcp__describe_tools` (or the full tool list) shows
   `extension__ontology__get_context`, and `registry__get_kind_schema` knows `ontology`. If not,
   stop with BLOCKED: an Atlan admin installs **Ontology** from the Store.
3. Find ontologies with `registry__list_artifacts` (kind `ontology`, no `workspace_id`): it
   lists every workspace you can read, and each row names its workspace, so you need no
   workspace listing first. With several, ask which one, recommending the one whose name or
   description matches the request.
4. Resolve the **release**: `registry__list_releases` with the ontology's `artifact_id` and
   `limit: 5`, and keep the `release_id` of the item whose `channel` is `latest` and
   `is_current` is true. It is under 1 KB. Never call `registry__get_artifact` on an ontology
   for this: it returns the whole model, which can be hundreds of thousands of characters, more
   than a host shows in one tool result. Every read below names that exact release. With no
   release, say the ontology has no published version yet: context and records modes stop
   there, and draft mode still works. Never read draft definitions as if they were published.

## 2. Context mode

1. Call `extension__ontology__get_context` with `ontology_id`, `release_id` and `query` (the
   person's business terms, not SQL). Keep `max_resources` at 20 or less and raise
   `max_depth` to 2 only when the answer needs a second hop.
2. When the question is about tables or joins, find the binding: `registry__list_artifacts`
   (kind `ontology_binding`), resolve its `latest` release with `registry__list_releases` the
   same way, and call
   `extension__ontology__get_binding_context` with `binding_id`, `release_id`, the same `query`
   and, when the binding was released against an older ontology release, `ontology_release_id`
   set to the one from step 1.4. A refusal there means a logical definition changed since the
   binding: say the binding needs updating rather than guessing the mapping.
3. Answer in the person's words: the definition, the relationships with their cardinality, and
   for each object the bound table and the join columns. Name the ontology and binding releases.
   Physical identities in a binding are declared metadata, not checked against the warehouse:
   before running SQL on them, check the columns exist with the warehouse query tool.

For an analytics answer, a released metric still defines the measure (the conventions' **Source
of truth**). The ontology and its bindings supply entity names and join paths; they never
override a metric's formula, population or filters.

## 3. Records mode

1. Call `extension__ontology__get_records` with `ontology_id`, `as_of` (the date the question is
   about, as a UTC instant; omit it for "now"), `limit: 10` (the default, 50 unranked records,
   can overflow a host), the `record_type_artifact_id` when known, and
   `include_knowledge_references: true` only when the answer needs its evidence. `query` is one
   or two key words, since every word must match: "on-time" finds records where "on-time
   performance definition" finds none.
2. Answer with each record that was in force, its validity interval and lifecycle state, and
   the exact document version it cites. Results are unranked, so with 10 back, narrow by record
   type or a more specific word. If the response says `scan_truncated: true`, say the list may
   be incomplete and narrow the query.
3. Never substitute a document's current text for the pinned version a record cites. When the
   person needs the text, read that exact version with the core `atlanai` plugin's content
   skill, or say it is unavailable.

## 4. Draft mode

The output is a document for a person to review and save in Ontology Studio. You prepare and
check it; you never save it.

1. **Scope.** Agree on the domain and the target ontology in one question. The ontology must
   exist: if none fits, ask the person to create one in Ontology Studio
   (`https://api.atlan.com/ontology/v1/ui/`) and tell you its name.
2. **Gather.** Start from what is already governed, in this order: released metrics and saved
   queries in the workspace (their populations and grains name the business objects), then
   the tables in scope with the warehouse query tool (`information_schema` columns and primary
   and foreign keys), then documents the person names. Read schemas only; never query business
   rows to draft a model.

   Read the metrics with `extension__metric__search_metrics` through `mcp__call_read_tool`:
   `workspace_id` of the ontology's workspace, the domain's business nouns as `query`,
   `include_definition: true` and `definition_limit: 2`. With `include_definition` it returns
   only released revisions, and each definition is up to 16 KiB, so never ask for more; search
   again with a narrower `query` for another one. For saved queries use
   `extension__metric__search_queries` with the same `workspace_id` and `query`, `limit: 5` and
   `include_sql: false`: their names and descriptions are enough here. When those tools are not
   listed, use `registry__search_artifacts` with kind `metric`, then `sql_query`, and that
   `workspace_id`, and count only rows with a released version. Send both searches in the
   same turn as step 1.4's release lookup. No published metrics is fine: say so and draft from
   the schemas. When step 1.4 found a release, read what is already modelled with
   `extension__ontology__get_context` (the domain's nouns as `query`, `max_resources` 20 or
   less), so the draft reuses its names instead of duplicating them.
3. **Write the document** as OWL in Turtle, in the subset Atlan imports:
   - `owl:Class` for each object type, with `rdfs:label` and `rdfs:comment`;
   - `owl:DatatypeProperty` for each property, with `rdfs:domain` and an XSD `rdfs:range`;
   - `owl:ObjectProperty` for each link, with `rdfs:domain`, `rdfs:range`, and `owl:inverseOf`
     when both directions have names;
   - stable IRIs under one base IRI the person agrees to.

   For terminology alone (a glossary, a code list), write SKOS instead: `skos:ConceptScheme`,
   `skos:Concept`, `skos:prefLabel`, `skos:definition`, `skos:broader`. Save the file in the
   working directory, for example `ontology-draft/<domain>.ttl`.
4. **Preview.** Call `extension__ontology__get_import_preview` with `ontology_id`, `document`,
   `format` (`owl` or `skos`) and `serialization` `turtle`. Fix every error in
   `validation_diagnostics` and preview again. Show the person the proposed object types and
   links, then the adapter's loss report: anything it could not represent is lost on save.
5. **Hand off.** Give the person the file and these steps: open Ontology Studio, **Import
   proposals**, paste or upload the file, review the destination and definitions, then **Save
   approved drafts**. Publishing a release and adding bindings from logical names to tables
   also happen in Studio, in that order. Offer the table mapping you found in step 2 as notes
   for the binding.

End with one status line from the conventions.
