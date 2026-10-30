---
name: atlan-ontology
description: "Use the organization's business ontology in Atlan: explain what a business object is and how it joins to others, with the tables each one is bound to; answer which policy, control or rule was in force on a date from governed records; or draft an ontology from warehouse tables, published metrics, documents or an OWL or SKOS file for a person to review and save in Ontology Studio. Use when a question names business entities, relationships, terminology or policies, or when someone wants to model their business before building metrics or a talk-to-data agent."
---

# Business ontology

Read [shared conventions](../atlan-analytics/references/conventions.md) first; its
**Registry calls**, **Asking questions**, **Status protocol** and **Safety** sections apply.
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

1. Open a registry session as the conventions describe, with an `objective` naming the domain.
2. Confirm the Ontology app is installed: `mcp__describe_tools` (or the full tool list) shows
   `extension__ontology__get_context`, and `registry__get_kind_schema` knows `ontology`. If not,
   stop with BLOCKED: an Atlan admin installs **Ontology** from the Store.
3. Pick the workspace with `registry__list_workspaces`, then find ontologies with
   `registry__list_artifacts` (kind `ontology`). With several, ask which one, recommending the
   one whose name or description matches the request.
4. Resolve the **release**: `registry__get_artifact` on the ontology with `channel` `latest`,
   and keep the `rel_…` release handle it resolves to. Every read below names that exact
   release. With no release, say the ontology has no published version yet: context and
   records modes stop there, and draft mode still works. Never read draft definitions as if
   they were published.

## 2. Context mode

1. Call `extension__ontology__get_context` with `ontology_id`, `release_id` and `query` (the
   person's business terms, not SQL). Keep `max_resources` at 20 or less and raise
   `max_depth` to 2 only when the answer needs a second hop.
2. When the question is about tables or joins, find the binding: `registry__list_artifacts`
   (kind `ontology_binding`), resolve its `latest` release the same way, and call
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
   about, as a UTC instant; omit it for "now"), an optional `query` and
   `record_type_artifact_id`, and `include_knowledge_references: true` when the answer needs its
   evidence.
2. Answer with each record that was in force, its validity interval and lifecycle state, and
   the exact document version it cites. If the response says `scan_truncated: true`, say the
   list may be incomplete and narrow the query.
3. Never substitute a document's current text for the pinned version a record cites. When the
   person needs the text, read that exact version with the core `atlan` plugin's content
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
