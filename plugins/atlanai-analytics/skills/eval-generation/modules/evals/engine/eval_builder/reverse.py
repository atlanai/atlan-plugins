"""Reverse-engineer the business question a query was written to answer.

Every eval gets TWO question forms, not one. The split is taken from a text-to-SQL
benchmark's task schema (`question_natural` / `question_spec`) and it is the whole design:

    question_natural  how a person would actually ask it, out loud, in a meeting
    question_spec     the same question with every decision pinned

A natural question is ambiguous on purpose. "What's our Q1 churn rate?" has
roughly thirty defensible answers — logo or ARR, denominator at period start or
end, churn recognised at notice or term end. The company picked one path years
ago, and an agent that does not know which path fails while writing perfectly
valid SQL. That gap is the thing worth measuring, so the natural form is what
the agent under test receives.

The spec form exists to prove the eval is *fair*: if a careful analyst holding
only the schema and the spec would write this exact SQL, the question has one
right answer and a failure is a real failure rather than an ambiguity. If no
spec can be written that pins the query down, the query should not be an eval.

The distance between the two forms is a property worth recording — it is
roughly "how many buried decisions does answering this require", which is the
same thing the selector's discrimination score estimates structurally.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from eval_builder.elements import VisualElement
from eval_builder.select import Candidate

MODEL = os.environ.get("EVAL_BUILDER_MODEL", "claude-opus-5")

_SYSTEM = """\
You reverse-engineer the business question that a SQL query was written to answer.

You will be given one SQL query and the schema of the tables it touches. Produce \
the question a person asked that led someone to write this query.

Return TWO forms of that question.

question_natural — how a person would genuinely ask it out loud. Short. Business \
vocabulary only. It may be ambiguous; real questions are. Do NOT mention column \
names, table names, SQL keywords, joins, filters, or values that appear as \
literals in the query. Someone reading it should not be able to reconstruct the \
query from its wording.

question_spec — the same question with every AMBIGUOUS decision pinned down, so \
that exactly one answer is correct. Pin the population, the exclusions, the time \
window, the grain, and any term whose meaning is contested. This form MAY state \
business rules explicitly ("excluding test stores and employee orders").

The test for including a clause: if you removed it, could a careful analyst \
arrive at a different answer? If not, leave it out. In particular do NOT restate \
the query's presentation — sort order, column order, rounding or decimal places, \
column labels, or the shape of the result. None of those can make an answer \
wrong, and listing them turns the spec into a transcription of the SELECT list, \
which is the one thing it must not be.

Also return:
decisions — the list of choices the query makes that a person answering the \
natural question could plausibly get wrong. One short phrase each.
as_of — if the query depends on a point in time, the ISO date it should be \
anchored to; otherwise null.
answerable — false if the query is not really answering a business question \
(a health check, a schema probe, a data dump), true otherwise.

Respond with JSON only, no prose, no code fences:
{"question_natural": str, "question_spec": str, "decisions": [str], \
"as_of": str|null, "answerable": bool}
"""

# A tile prompt shares everything from the two forms on; only the framing
# differs. The query-history prompt above is left exactly as it was.
_TILE_SYSTEM = """\
You reverse-engineer the business question that a dashboard tile answers.

You will be given one tile: what it displays, how it is broken down and \
filtered, the columns it reads with their descriptions, and the SQL that \
computes it. Produce the question a person looking at this tile asks, which \
this SQL answers.

The tile's own words — its title and its measure, dimension and filter labels \
— are what the business calls these things, and question_natural MAY use them: \
that is not a leak. Schema identifiers may NOT appear: no table or column name \
as the SQL writes it.

""" + _SYSTEM[_SYSTEM.index("Return TWO forms"):]


# ---------------------------------------------------------------------------
# The model step is the harness's job, not this module's
# ---------------------------------------------------------------------------
#
# This package is run as a skill INSIDE a coding agent. The agent reading these
# instructions is the model, so there is no client to construct and no API key
# to hold: spawning `claude -p` from here would be an agent shelling out to a
# second copy of itself, paying twice and reasoning with less context than the
# caller already has.
#
# So the module supplies the deterministic halves — build the prompt, validate
# the answer — and the harness supplies the thinking in between:
#
#     for each candidate:
#         prompt = build_prompt(candidate, schema)      # here
#         answer = <the agent answers it directly>      # the harness
#         result = parse_response(answer, candidate)    # here, with leak checks
#
# `reverse_engineer()` below keeps an optional client argument so the module
# stays testable outside a harness, but nothing in the normal path uses it.


def _vocabulary_context(vocabulary: dict | None) -> str:
    """The words the business already uses for this data.

    Without this the drafter is told what it may NOT say and given nothing it
    MAY say, so it paraphrases around the subject: one run answered a question
    about orders with "the things people bought", when the workbook it came
    from is called Order Activity. The leak check was never the obstacle --
    it only ever blocked the literal identifier `SALES.ORDERS` --
    the vocabulary simply never arrived.

    Measure labels matter most. A dashboard's dimension labels are usually its
    column names prettified, but labels like "% Returned" or "Average Basket
    Size" name a computed concept with no column behind it. Drop those and the
    concept becomes unnameable.
    """
    if not vocabulary:
        return ""
    lines = []
    for key, heading in (("titles", "This data is presented under these names"),
                         ("measures", "The business calls these measures"),
                         ("filters", "People filter this page by")):
        vals = [v for v in (vocabulary.get(key) or []) if v]
        if vals:
            lines.append(f"{heading}: {', '.join(dict.fromkeys(vals))}.")
    if not lines:
        return ""
    return (
        "\n\nVocabulary from the report this query powers. Prefer these words over "
        "your own paraphrase — they are what a person in this company actually says. "
        "Using one is not a leak: naming the literal column identifier is.\n"
        + "\n".join(lines)
    )


def _is_word(label: str | None) -> bool:
    """A label a person says, not an identifier-shaped field name."""
    return bool(label and label.strip()) and not _IDENT_LIKE.fullmatch(label.strip())


def _tile_vocabulary(element: VisualElement) -> str:
    """The tile's own words, in place of the workbook-wide list.

    The global list put a headline measure in every prompt as one of hundreds
    of names and never said which tile the SQL was, and the tile's own measure
    label was in no list at all.
    """
    groups = (("The tile is titled", [element.title]),
              ("Its measures are labelled", [m.label for m in element.displayed_measures]),
              ("Its breakdown is labelled", [d.label for d in element.dimensions]),
              ("Its filters are labelled", [f.label for f in element.filters]))
    lines = []
    for head, labels in groups:
        words = list(dict.fromkeys(w.strip() for w in labels if _is_word(w)))
        if words:
            lines.append(f"{head}: {', '.join(words)}.")
    if not lines:
        return ""
    return (
        "\n\nVocabulary from the tile itself. Prefer these words over your own "
        "paraphrase — they are what a person in this company actually says. "
        "Using one is not a leak: naming a column identifier is.\n"
        + "\n".join(lines)
    )


def build_prompt(candidate: Candidate, schema: dict[str, list[dict]] | None = None,
                 vocabulary: dict | None = None) -> str:
    """The full instruction for one query, for the harness agent to answer.

    A tile's prompt opens with the tile -- what it displays, every column it
    reads -- before its SQL, and its vocabulary is the tile's own; `vocabulary`
    (a source-wide list) is for query history only.
    """
    el = candidate.element
    if el is not None:
        return (
            f"{_TILE_SYSTEM}\n\n"
            f"---\n\n"
            f"{el.summary()}\n\n"
            f"Columns the tile reads:\n{_tile_columns(el, schema)}\n\n"
            f"SQL:\n{candidate.sql}"
            f"{_tile_vocabulary(el)}\n\n"
            f"Structural signals already detected in this query: "
            f"{', '.join(candidate.modes) or 'none'}."
        )
    return (
        f"{_SYSTEM}\n\n"
        f"---\n\n"
        f"SQL:\n{candidate.sql}\n\n"
        f"Schema of the tables it touches:\n{_schema_context(candidate, schema)}"
        f"{_vocabulary_context(vocabulary)}\n\n"
        f"Structural signals already detected in this query: "
        f"{', '.join(candidate.modes) or 'none'}."
    )


def parse_response(text: str, candidate: Candidate) -> ReverseResult:
    """Validate one answer and run the leak checks against it."""
    data = _parse_json(text)
    result = ReverseResult(
        question_natural=(data.get("question_natural") or "").strip(),
        question_spec=(data.get("question_spec") or "").strip(),
        decisions=[d for d in (data.get("decisions") or []) if isinstance(d, str)],
        as_of=data.get("as_of") or None,
        answerable=bool(data.get("answerable", True)),
    )
    result.leaks = find_leaks(result.question_natural, candidate)
    return result


@dataclass
class ReverseResult:
    question_natural: str
    question_spec: str
    decisions: list[str] = field(default_factory=list)
    as_of: str | None = None
    answerable: bool = True
    leaks: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.answerable and not self.leaks and bool(self.question_natural)


# --------------------------------------------------------------------------
# Leak detection
# --------------------------------------------------------------------------

_SQL_TOKENS = re.compile(
    r"\b(select|from\s+the\s+table|where\s+clause|group\s+by|order\s+by|having|"
    r"case\s+when|count\(|sum\(|avg\(|coalesce|nullif|inner\s+join|left\s+join|cte)\b",
    re.I,
)
# Relational verbs describe the *shape* of the query rather than the question.
# "joined" and "grouped" never survive a \bjoin\b token match but give away just
# as much: a person asking a question does not say how the tables connect.
_RELATIONAL_VERBS = re.compile(
    r"\b(join(ed|ing)?|group(ed|ing)?\s+by|filter(ed|ing)?\s+(on|by)|"
    r"aggregat(e|ed|ing)|dedup(e|ed|ing)?|pivot(ed|ing)?)\b",
    re.I,
)
_IDENT_LIKE = re.compile(r"\b[a-z]+_[a-z_]+\b", re.I)  # snake_case identifiers

# Prefixes that carry no business meaning, so stripping them is what makes
# FCT_V2_ORDER_EVENTS comparable to the phrase "order events".
_TABLE_NOISE = re.compile(r"^(fct|dim|stg|agg|mart|tbl|vw|f|d)_|_(v\d+|snapshot|daily|current)$", re.I)


# Table names that are also the ordinary word for the thing. Saying "ticket" in
# a support question is speaking English, not quoting a schema.
_BUSINESS_NOUNS = frozenset({
    "ticket", "tickets", "account", "accounts", "order", "orders", "customer",
    "customers", "user", "users", "opportunity", "product", "products", "call",
    "calls", "invoice", "invoices", "payment", "payments", "subscription",
    "subscriptions", "contact", "contacts", "lead", "leads", "campaign",
    "campaigns", "event", "events", "issue", "issues", "store", "stores",
})


def _is_distinctive(leaf: str) -> bool:
    """Is naming this table actually a leak, or just using the right noun?

    The general rule, not a word list: a single-token table name is almost
    always the ordinary word for the thing — TICKET, ALERTS, RELEASES, ORDERS,
    USERS — and a question about support work says "ticket" because that is
    English. A multi-token name is a schema artifact: nobody says
    "fct_account_funnel" in a meeting, so quoting one is a real leak.

    A curated list was tried first and kept needing new entries (ticket, then
    releases, then alerts). The token-count rule covers the same cases without
    a maintenance burden, and errs toward accepting a question rather than
    pushing the generator into evasive phrasing.
    """
    if len(leaf) < 4:
        return False              # aliases like `d` or `tc` match everything
    if leaf in _BUSINESS_NOUNS:
        return False
    if "_" not in leaf:
        return False              # single-token name: the ordinary word
    return True


def _humanize(identifier: str) -> str:
    """FCT_V2_ORDER_EVENTS -> 'order events'."""
    name = _TABLE_NOISE.sub("", identifier.lower())
    parts = [p for p in name.split("_") if p and not re.fullmatch(r"[a-z]?\d+|[a-z]{1,3}", p)]
    return " ".join(parts)


def find_leaks(question: str, candidate: Candidate) -> list[str]:
    """Reasons this question would hand the agent its own answer.

    A natural question that names the table and the filter column is not
    measuring whether the agent knows the business; it is measuring whether it
    can read. That eval passes for the wrong reason.
    """
    leaks: list[str] = []
    if candidate.element is not None:
        # The tile's own words are the business's vocabulary and may be said.
        # The checks run on what is left, so every identifier rule still holds.
        for word in _tile_words(candidate.element):
            question = re.sub(rf"(?<!\w){re.escape(word)}(?!\w)", " ", question, flags=re.I)
    q = question.lower()

    if _SQL_TOKENS.search(question):
        leaks.append("contains SQL syntax")
    if _RELATIONAL_VERBS.search(question):
        leaks.append("contains SQL syntax: describes the query shape, not the question")

    for table in candidate.tables:
        leaf = table.split(".")[-1].lower()
        if not _is_distinctive(leaf):
            # A table called TICKET is the business vocabulary, not a leak: a
            # person asking about support work says "ticket". Flagging it would
            # push the generator into writing worse, more evasive questions.
            # Short aliases are worse — a table aliased `d` matched the letter
            # "d" anywhere in the sentence.
            continue
        if re.search(rf"\b{re.escape(leaf)}\b", q):
            leaks.append(f"names the table {leaf}")
            continue
        # Also catch the spoken form: "activity tracker" for FCT_..._ACTIVITY_TRACKER
        human = _humanize(leaf)
        if len(human.split()) >= 2 and human in q:
            leaks.append(f"names the table {leaf} (as {human!r})")

    # snake_case tokens in a spoken question are almost always column names
    for ident in set(_IDENT_LIKE.findall(question)):
        if ident.lower() in candidate.sql.lower():
            leaks.append(f"names the identifier {ident}")

    return sorted(set(leaks))


def _tile_words(element: VisualElement) -> list[str]:
    """Title and labels, longest first so a label inside the title goes with it."""
    words = [element.title, *(m.label for m in element.measures),
             *(d.label for d in element.dimensions), *(f.label for f in element.filters)]
    return sorted({w.strip() for w in words if _is_word(w)}, key=len, reverse=True)


# --------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------

def _schema_context(candidate: Candidate, schema: dict[str, list[dict]] | None) -> str:
    """Only the tables this query touches. More schema is leakage, not help."""
    if not schema:
        return "(schema unavailable — infer vocabulary from the query)"
    blocks = []
    for table in candidate.tables:
        cols = schema.get(table) or schema.get(table.upper()) or []
        if not cols:
            continue
        lines = [f"{table}:"]
        for c in cols[:60]:
            note = f"  -- {c['comment']}" if c.get("comment") else ""
            lines.append(f"  {c['column']} {c['type']}{note}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "(no schema found for these tables)"


def _tile_columns(element: VisualElement, schema: dict[str, list[dict]] | None) -> str:
    """Every column the tile reads, with its type and description, and no other.

    The 60-column cut above dropped the very columns a KPI reads: on a wide
    table a KPI's measure and filter columns can sit hundreds of columns in.
    A tile names what it reads, so that is the whole list.
    """
    tables: dict[str, dict[str, dict]] = {}
    for table, cols in (schema or {}).items():
        tables[table.upper()] = {str(c.get("column", "")).upper(): c for c in cols}
    lines = []
    for qualified in element.physical_columns:
        table, _, column = qualified.rpartition(".")
        t = table.upper()
        # Keyed as the adapter keyed it; tolerate a database part on one side only.
        cols = tables.get(t) or next((v for k, v in tables.items()
                                      if k.endswith("." + t) or t.endswith("." + k)), {})
        c = cols.get(column.upper()) or {}
        kind = f" {c['type']}" if c.get("type") else ""
        note = f"  -- {c['comment']}" if c.get("comment") else ""
        lines.append(f"  {qualified}{kind}{note}")
    return "\n".join(lines) or "  (no physical column traced)"


def reverse_engineer(
    candidate: Candidate,
    schema: dict[str, list[dict]] | None = None,
    client=None,
    model: str = MODEL,
) -> ReverseResult:
    """Turn one mined query into a question pair. Raises on transport failure."""
    if client is None:
        raise RuntimeError(
            "no client: inside a harness use build_prompt() / parse_response() and "
            "answer the prompt directly. Pass an Anthropic-style client only when "
            "running this module outside a harness."
        )

    user = build_prompt(candidate, schema)

    resp = client.messages.create(
        model=model,
        max_tokens=1500,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return parse_response(text, candidate)


def _parse_json(text: str) -> dict:
    """Models wrap JSON in fences often enough that stripping them is not optional."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise
        return json.loads(m.group(0))
