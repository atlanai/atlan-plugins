"""The eval record contract, shared by the builder and the runner.

One deliberate decision lives here: a record stores a question and the SQL that
answers it, never a frozen answer value. The warehouse keeps moving, so a number
captured at build time rots. The runner executes `golden_sql` at run time to get
the expected value, and compares the agent's answer against that.

This also makes a dataset portable: the same record is valid against a clone, a
synthetic fixture, or production, as long as the schema holds.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass
class Provenance:
    """Where this eval came from. Without it, a reviewer cannot judge the record."""

    source: str                      # "query_history" | "bi:sigma" | "bi:tableau" | "bi:powerbi" | "skill_suite"
    query_id: str | None = None      # warehouse query id, when mined
    first_seen: str | None = None    # ISO8601
    last_seen: str | None = None
    run_count: int = 0
    distinct_users: int = 0
    # why the selector kept this query, per dimension — a reviewer reads this
    selection_scores: dict[str, float] = field(default_factory=dict)
    selection_notes: list[str] = field(default_factory=list)


@dataclass
class EvalRecord:
    """One (question, golden SQL) pair plus everything needed to review it."""

    id: str
    question: str
    golden_sql: str
    tables: list[str] = field(default_factory=list)
    # an as-of anchor makes a time-relative query deterministic across runs
    as_of: str | None = None
    # the failure modes this eval exercises; becomes `categories` on the row
    modes: list[str] = field(default_factory=list)
    # the decision-pinned form, kept for review and for judging fairness
    question_spec: str = ""
    decisions: list[str] = field(default_factory=list)
    provenance: Provenance | None = None
    # set by a human in the review step; only approved records get pushed
    review_status: str = "pending"   # pending | approved | rejected
    review_note: str = ""
    # the dialect golden_sql is written in, from its source's connector; the
    # runner executes it on that warehouse and never translates it
    dialect: str = "snowflake"
    # The tile this eval was built from (`VisualElement.to_dict()`), and the
    # SQL as crawled before it was cut down; both None for query history.
    element: dict | None = None
    original_sql: str | None = None

    # ---- the firewall -------------------------------------------------
    # A record holds both the question and its answer, so handing the whole
    # object to a runner is how an eval set leaks. These two views are
    # disjoint by construction. The pattern is taken from a text-to-SQL
    # benchmark's task contract, which splits the same way for the same reason.
    #
    # Runner code calls agent_view(). Grader code calls oracle_view().
    # Nothing calls both.

    def agent_view(self, bounded_tables: bool = False) -> dict[str, Any]:
        """Everything the agent under test may see. Never the SQL or the answer."""
        view: dict[str, Any] = {"id": self.id, "question": self.question}
        if self.as_of:
            # The anchor is part of the question, not a hint: without it the
            # question is ambiguous rather than hard.
            view["as_of"] = self.as_of
        if bounded_tables:
            view["tables"] = list(self.tables)
        return view

    def oracle_view(self) -> dict[str, Any]:
        """Grader-only. Importing this into runner code is the bug to avoid."""
        return {
            "id": self.id,
            "golden_sql": self.golden_sql,
            "dialect": self.dialect,
            "as_of": self.as_of,
            "tables": list(self.tables),
        }

    def content_hash(self) -> str:
        """Stable identity for dedupe: the question and the SQL, nothing else.

        So a record whose golden SQL changed is a new record. Re-pushing a set
        rebuilt from tiles into a dataset holding an older build's full-SQL
        rows adds rows beside them rather than replacing them: archive the old
        rows first, or push to a new dataset.
        """
        payload = json.dumps(
            {"question": self.question.strip(), "golden_sql": _normalize_sql(self.golden_sql, self.dialect)},
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_registry_record(self, input_key: str = "question",
                           dataset_name: str = "") -> dict[str, Any]:
        """Shape for POST /eval/v1/datasets/{id}/records.

        Field names are taken from the gateway's own OpenAPI, not the prose
        docs, which disagree: the body uses `expected` (not `expected_output`)
        and `extra` (not `metadata` — that is an envelope key, so the row's bag
        is called extra).

        `golden_sql` rides in `expected` rather than a literal answer value,
        because the runner is what turns SQL into a value at run time.

        Failure modes go in `categories`, which the gateway describes as "the
        slice keys a deep-dive filters scores by" — so a score report can be
        broken down by date_window vs join_path without any extra plumbing.
        """
        digest = self.content_hash()
        # The gateway makes `name` unique per (workspace, kind) — NOT per
        # dataset. Two datasets in one workspace holding the same question
        # therefore collide, and every record in the second push is rejected.
        # Folding the dataset into the handle is what keeps them distinct.
        handle = hashlib.sha256(f"{dataset_name}\x00{digest}".encode()).hexdigest()
        return {
            "name": _slug(self.id, self.question, handle),
            "display_name": self.question[:200],
            "input": {input_key: self.question},
            "expected": {"golden_sql": self.golden_sql, "dialect": self.dialect, "as_of": self.as_of},
            "categories": sorted(self.modes),
            "label": self.review_status,
            "source_kind": "external",
            # A tile's identity, so the rows built from one tile (and its
            # filter variants) can be found together; otherwise the record id.
            "source_ref": (self.element or {}).get("element_id") or self.id,
            "extra": {
                "tables": self.tables,
                "question_spec": self.question_spec,
                "decisions": self.decisions,
                "provenance": asdict(self.provenance) if self.provenance else None,
                "content_hash": digest,
                **({"element": self.element} if self.element else {}),
            },
        }


def _slug(record_id: str, question: str, digest: str = "") -> str:
    """Kebab-case machine handle, unique per (workspace, kind) per the gateway.

    The readable part is truncated, so two questions sharing a long prefix
    ("...distributed across performance bands" / "...across regions") would
    collide and the second create would be rejected. A short digest of the full
    content keeps the handle readable and still unique.
    """
    base = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    tail = re.sub(r"[^a-z0-9]+", "-", record_id.lower()).strip("-")
    stem = f"case-{tail}-{base}"[:52].rstrip("-")
    return f"{stem}-{digest[:6]}" if digest else stem


def _normalize_sql(sql: str, dialect: str = "snowflake") -> str:
    """Whitespace- and case-insensitive form, so cosmetic edits don't fork identity."""
    try:
        import sqlglot

        return sqlglot.transpile(sql, read=dialect, write=dialect, pretty=False)[0].lower()
    except Exception:  # noqa: BLE001 - unparseable SQL still needs an identity
        return " ".join(sql.lower().split())


@dataclass
class EvalSpec:
    """A build's output: the candidate set, pre-review."""

    name: str
    source_description: str
    records: list[EvalRecord] = field(default_factory=list)

    def dedupe(self) -> EvalSpec:
        seen: dict[str, EvalRecord] = {}
        for r in self.records:
            h = r.content_hash()
            if h in seen:
                # keep the better-evidenced one
                if r.provenance and seen[h].provenance:
                    if r.provenance.run_count > seen[h].provenance.run_count:
                        seen[h] = r
                continue
            seen[h] = r
        self.records = list(seen.values())
        return self

    def to_json(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "source_description": self.source_description,
                "records": [asdict(r) for r in self.records],
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, text: str) -> EvalSpec:
        d = json.loads(text)
        records = []
        for r in d.get("records", []):
            prov = r.pop("provenance", None)
            records.append(EvalRecord(
                **known_fields(EvalRecord, r),
                provenance=Provenance(**known_fields(Provenance, prov)) if prov else None))
        return cls(d["name"], d.get("source_description", ""), records)


def known_fields(cls, d: dict) -> dict:
    """The keys `cls` declares. A newer writer adds fields, and an older reader
    must still load its file rather than raise on the first one it lacks."""
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}
