# The metric card contract

A metric card says what a number *is*, in fields a consumer can build SQL from. This is why
each field holds what it holds, and what the agent refuses.

The authority is the Agent Registry's definition of the metric kind. The Registry follows the
open [ai-registry specification](https://github.com/atlanai/ai-registry): each artifact's `spec`
belongs to its kind and is checked against that kind's definition when it is published
([`spec/file-spec.md`, section 3.4](https://github.com/atlanai/ai-registry/blob/main/spec/file-spec.md#34-versionartifactjson)).
The metric kind declares a fixed set of 24 fields and rejects any field it does not declare.
**The card uses those fields and invents nothing.** A fact that needs a 25th field is a fact
this estate does not publish.

---

## The fields that hold SQL

| field | holds | not |
|---|---|---|
| `formula` | the measure, one expression: `COUNT(DISTINCT …ID)`, or a whole guarded divide | a sentence describing the measure |
| `grain` | the key column(s) of the base source, comma-separated, fully qualified | "one value per customer per month" |
| `source.sql` | **the FROM clause** : the text after `FROM` | a worked example of the query |
| `mandatory_filters` | predicates without which it is a different metric | prose restrictions |
| `filters` | predicates a caller may drop | : |
| `dimensions[].sql` | the expression to group by, fully qualified | a column name alone |
| `joins[].sql` | the ON condition | : |

Everything else : `description`, `population`, `exclusions`, `notes` : is for a person, and
prose is exactly right there.

### `source.sql` is the FROM clause

The registry's `Source` is *"a table or SQL source"* and takes exactly one of `table` and
`sql`. So `sql` is not a statement; it is what goes after `FROM`:

```
ACME.MARTS.FCT_ORDERS
INNER JOIN ACME.MARTS.DIM_CUSTOMER
        ON ACME.MARTS.FCT_ORDERS.CUSTOMER_ID = ACME.MARTS.DIM_CUSTOMER.ID
```

Three things follow, and they remove every reason to invent a field:

- **A join's `INNER` or `LEFT` is written here**, because `joins[]` declares only `name`,
  `to`, `sql`, `cardinality` and `origin` : and inner-versus-left changes the number.
  `joins[]` stays as the declaration a reader and `required_join` use.
- **A measure computed in two passes is derived tables joined together.** Ordinary SQL. No
  `steps` field is needed, and none exists.
- **A source this model does not decompose** : a `LATERAL FLATTEN`, a table function : is
  source text, which is what the field is for.

**Never alias a table here.** Every expression on the card is fully qualified, and a
warehouse drops the original name the moment a table is aliased. A derived table is the
exception: it has no name of its own, so it must be given one.

Leave it empty when the source is one plain table : `source.entity` already names it.

### Parameters are `{{name}}`

A value that is meant to move : a window, a campaign, a tenant : is a parameter declared in
`source.parameters` with the literal that was there as its default. Two refusals, in both
directions: a marker nothing declares, and a declaration no expression uses. The second is
the one that lies, because it advertises a control that does nothing.

A **date literal is refused**. A frozen date is a metric that was right the day it was
written and narrower every day after.

---

## What the agent refuses, and why

`x3_emit` is a gate. A card that fails any of these does not reach the registry, and the
message names the card and the field.

| refusal | because |
|---|---|
| a reference wearing a query alias | `m.amount` means nothing away from the query that declared it |
| `description` containing SQL | it is the third retrieval tier; spend it on words somebody would type |
| `domain` that is a warehouse schema | a business domain is not a path |
| `source.sql` starting with `SELECT` | it is the FROM clause |
| `source.sql` aliasing a named table | it makes every qualified reference on the card unresolvable |
| a declared join with no FROM clause | the join's kind has nowhere else to live |
| `aggregation.type` disagreeing with `formula` | `count` over a `COUNT(DISTINCT …)` says something untrue about itself |
| a name that carries its own cut : `X by <one of its dimensions>` | nobody searching for the measure can find it |
| an example naming a statement that is not one of this metric's sources | an example is evidence somebody ran *this* |

Three things are **reported, not refused**, because no rule can decide them from the card
alone: a name whose leading word is also a dimension (that is how the estate names a measure
after the entity it is about), a name that reads as a cut though the qualifier is not one of
its dimensions, and a name that hides a definitional restriction.

---

## Repair without a person

A refusal is written to `x3_repair.json` : the card as written, the exact field, why, and a
hint. That is a prompt, not a traceback, so the loop closes without anybody carrying the
message:

```bash
python scripts/x3_loop.py --run <run> --from 'answers/*.json' --author "<command>"
```

`--author` is the operator's command; the stage holds no model key. Four guards, because the
loop is autonomous: a round cap, an oscillation stop when a round refuses exactly what the
last one did, **no widening** (only cards that failed may come back), and a verdict rather
than a half-written layer.

---

## Identity

**A metric's `source_id` is derived from what it measures, never from its name.**

If the name were part of the identity, renaming a set of metrics would publish every one of
them as a new artifact, and the old ones would linger beside them. Worse, the Registry keeps
an archived name reserved and hides it from every listing, so the next push would collide with
names nobody can see.

Two rules follow:

- **A rename is a revision.** Identity does not move when the label does.
- **A push never creates silently.** `plan_push` separates revisions from creates before
  anything is written; `r3b_push` stops and names them. `--allow-new` is the explicit answer
  for a genuinely new metric, because a create freezes a name for ever.

And a name collision is never answered by renaming. A 409 fails loudly: a name is a decision
about what the metric is called, not something a retry loop should invent.

### The agent does not get to decide what is new

Identity is minted from four parts: entity, FROM clause, formula and mandatory filters. Each
one is needed. Entity and formula alone are not enough: several coverage measures can be one
`MAX` over one table that differ only in which key they count, which is a mandatory filter;
and two measures can share entity, formula and filters but read different tables, which is
the FROM clause.

Two cards that agree on all four are refused at authoring time. They are not two metrics,
whatever their names say, and publishing them mints one artifact that the second silently
overwrites.

But a minted identity is still a *guess* about which artifact this is, and the registry is
what knows. So before a push decides anything, `resolve_against_live` points each item at
the artifact that already measures the same thing:

1. an exact `source_id` match is a revision;
2. otherwise match on **what it measures**, and on a unique hit adopt that artifact's
   identity **and its name**: a name is frozen at create, so sending a different one asks
   for a rename nobody can grant;
3. when **two live artifacts measure the same thing**, the name breaks that tie between
   them, by dropping leading words, fewest first. That is the only place a name is acted
   on, and both candidates already measure the same thing, so picking one is strictly
   better than failing.
4. anything still unmatched is reported, with the live artifact its name resembles if there
   is one, **and nothing is adopted**. A name that ends with another metric's name is not
   evidence of a rename: a new `Regional Open Pipeline ARR` would land on top of
   `Open Pipeline ARR` and overwrite it, which is worse than the create it was avoiding.
   That is a decision for a person, and a create needs `--allow-new`.

A layer that has been published once re-publishes as revisions only, with no creates.
