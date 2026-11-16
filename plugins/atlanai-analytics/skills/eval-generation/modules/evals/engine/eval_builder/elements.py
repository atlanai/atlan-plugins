"""The visual element: the unit an eval is built for.

A dashboard tile is what a person looks at and asks about, so it is the source
of truth for a BI-derived eval: its title, its kind, the measures and
dimensions it displays, the filters it applies. The SQL is derived from, or
pruned to, exactly that, and the question is drafted from the tile, not from
the SQL alone.

This module is the contract shared by the source adapters (`sources/sigma`,
`sources/tableau`, `sources/powerbi`) and the pipeline (`select`, `reverse`, `cli`, `spec`). It
imports nothing from the rest of the package so every side can depend on it.

Everything here is deterministic data. No adapter may fill a field by guessing:
a measure whose physical columns cannot be traced is a refusal, not an empty
list.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum


class ElementKind(str, Enum):
    """Normalised tile kinds, shared by every BI tool.

    `raw_kind` on the element keeps the tool's own string (`Viz(kpi)`,
    `Pivot Table`, a Tableau mark class) for provenance.
    """

    KPI = "kpi"
    SINGLE_VALUE = "single_value"
    GAUGE = "gauge"
    FUNNEL = "funnel"
    DONUT = "donut"
    PIE = "pie"
    BAR = "bar"
    LINE = "line"
    AREA = "area"
    SCATTER = "scatter"
    COMBO = "combo"
    SANKEY = "sankey"
    BOX = "box"
    WATERFALL = "waterfall"
    MAP = "map"
    PIVOT = "pivot"
    TABLE = "table"
    TEXT_TABLE = "text_table"
    UNKNOWN = "unknown"

    @property
    def is_single_value(self) -> bool:
        """The tile shows one number (or a few labelled numbers), no breakdown."""
        return self in (ElementKind.KPI, ElementKind.SINGLE_VALUE, ElementKind.GAUGE)

    @property
    def is_chart(self) -> bool:
        return self in (
            ElementKind.FUNNEL, ElementKind.DONUT, ElementKind.PIE, ElementKind.BAR,
            ElementKind.LINE, ElementKind.AREA, ElementKind.SCATTER, ElementKind.COMBO,
            ElementKind.SANKEY, ElementKind.BOX, ElementKind.WATERFALL, ElementKind.MAP,
        )

    @property
    def is_tabular(self) -> bool:
        return self in (ElementKind.PIVOT, ElementKind.TABLE, ElementKind.TEXT_TABLE)


# Sigma stores `Table`, `Pivot Table` or `Viz(<vizualizationType>)` in
# sigmaDataElementType. These are every value seen across a large Sigma deployment.
_SIGMA_VIZ = {
    "kpi": ElementKind.KPI, "singlevalue": ElementKind.SINGLE_VALUE,
    "gauge": ElementKind.GAUGE, "funnel": ElementKind.FUNNEL,
    "donut": ElementKind.DONUT, "pie": ElementKind.PIE, "bar": ElementKind.BAR,
    "line": ElementKind.LINE, "area": ElementKind.AREA, "scatter": ElementKind.SCATTER,
    "combo": ElementKind.COMBO, "sankey": ElementKind.SANKEY, "box": ElementKind.BOX,
    "waterfall": ElementKind.WATERFALL, "region": ElementKind.MAP, "point": ElementKind.MAP,
    "geography": ElementKind.MAP, "treemap": ElementKind.UNKNOWN,
}


def kind_from_sigma(raw: str | None) -> ElementKind:
    t = (raw or "").strip()
    if t.lower() == "table":
        return ElementKind.TABLE
    if t.lower() == "pivot table":
        return ElementKind.PIVOT
    m = re.fullmatch(r"Viz\((\w+)\)", t, re.I)
    if m:
        return _SIGMA_VIZ.get(m.group(1).lower(), ElementKind.UNKNOWN)
    return ElementKind.UNKNOWN


def kind_from_tableau(mark_class: str | None, *, continuous_axes: int = 0,
                      date_axis: bool = False, has_dimensions: bool = True,
                      is_map: bool = False) -> ElementKind:
    """Tableau's mark class, with its `Automatic` resolved by a stated rule:
    a continuous date against a measure is a line, one continuous axis is a
    bar, no continuous axis is a text table, or a KPI when nothing breaks the
    number down."""
    c = (mark_class or "Automatic").strip().lower()
    table = {"bar": ElementKind.BAR, "line": ElementKind.LINE, "area": ElementKind.AREA,
             "circle": ElementKind.SCATTER, "shape": ElementKind.SCATTER,
             "square": ElementKind.TEXT_TABLE, "heatmap": ElementKind.TEXT_TABLE,
             "pie": ElementKind.PIE, "text": ElementKind.TEXT_TABLE,
             "map": ElementKind.MAP, "polygon": ElementKind.MAP, "gantt": ElementKind.BAR}
    if c != "automatic":
        kind = table.get(c, ElementKind.UNKNOWN)
        if kind is ElementKind.TEXT_TABLE and not has_dimensions:
            return ElementKind.KPI
        return kind
    if is_map:
        return ElementKind.MAP
    if date_axis and continuous_axes >= 1:
        return ElementKind.LINE
    if continuous_axes >= 1:
        return ElementKind.BAR
    return ElementKind.TEXT_TABLE if has_dimensions else ElementKind.KPI


@dataclass
class Measure:
    """One number the tile displays, at the tile's grain."""

    label: str                                  # the tile's own words: "Sum of Order Amount"
    expr: str                                   # SQL expression in `VisualElement.dialect`: SUM(AMOUNT)
    agg: str | None = None                      # SUM|COUNT|COUNT_DISTINCT|AVG|MIN|MAX|CUSTOM|None
    physical_columns: list[str] = field(default_factory=list)   # DB.SCHEMA.TABLE.COLUMN
    role: str = "value"                         # value|target|comparison|subtotal|helper
    source_ref: str | None = None               # Sigma column id / Tableau column-instance name
    description: str | None = None              # catalog description, when known


@dataclass
class Dimension:
    """One axis the tile breaks the measures down by."""

    label: str
    expr: str
    physical_columns: list[str] = field(default_factory=list)
    bucket: str | None = None                   # day|week|month|quarter|year, for dates
    axis: str | None = None                     # x|y|color|rows|columns|stage|detail
    source_ref: str | None = None
    description: str | None = None


@dataclass
class FilterSpec:
    """A filter the tile applies, or carries without applying."""

    expr: str | None                            # the SQL predicate as applied; None when not applied
    label: str | None = None                    # the control/field caption, when known
    column: str | None = None                   # the physical column, when it is a plain one
    kind: str = "predicate"                     # categorical|range|relative_date|top_n|boolean|predicate
    values: list[str] = field(default_factory=list)
    scope: str = "element"                      # element|page|dashboard|datasource|extract|workbook|source|security
    origin: str = ""                            # sigma-sql|tableau-xml|tableau-tds
    state: str = "set"                          # set|unset|unknown
    applied: bool = True                        # False: recorded for the reviewer, not in the SQL
    injectable: bool = False                    # a page filter whose values are known, so variants can be made
    # Every physical column `expr` reads, when it is not one plain `column`:
    # IFF(SHIPPED_AT IS NOT NULL, ..., STATUS) IN (...) reads two.
    physical_columns: list[str] = field(default_factory=list)


@dataclass
class VisualElement:
    """A tile, with what it displays and the SQL that computes it."""

    tool: str                                   # "sigma" | "tableau" | "powerbi"
    workbook: str
    page: str | None
    element_id: str                             # IDENTITY: Sigma node id / "<workbook luid>:<worksheet>"
    title: str
    kind: ElementKind
    raw_kind: str
    workbook_id: str | None = None
    measures: list[Measure] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)
    filters: list[FilterSpec] = field(default_factory=list)
    source_relation: list[str] = field(default_factory=list)   # physical tables the tile SQL reads
    dialect: str = "snowflake"
    original_sql: str | None = None             # as crawled / the datasource's custom SQL
    sql: str | None = None                      # the tile SQL; None when refused
    shape: str | None = None                    # Sigma F1..F6 / Tableau "shelves"
    refusal: str | None = None                  # why there is no tile SQL
    notes: list[str] = field(default_factory=list)
    provenance: dict = field(default_factory=dict)   # tenant, export path, crawled_at, versions

    # -- derived -------------------------------------------------------------

    @property
    def key(self) -> str:
        """A readable slug for file names. Not unique: titles repeat. Use
        `element_id` for identity."""
        return "--".join(_slug(p) for p in (self.page or "", self.title) if p) or _slug(self.element_id)

    @property
    def physical_columns(self) -> list[str]:
        cols: set[str] = set()
        for m in self.measures:
            cols.update(m.physical_columns)
        for d in self.dimensions:
            cols.update(d.physical_columns)
        for f in self.filters:
            if f.column:
                cols.add(f.column)
            cols.update(f.physical_columns)
        return sorted(cols)

    @property
    def displayed_measures(self) -> list[Measure]:
        return [m for m in self.measures if m.role in ("value", "target", "comparison")]

    @property
    def usable(self) -> bool:
        return bool(self.sql) and not self.refusal

    def summary(self) -> str:
        """The tile in words, for the drafter and the reviewer. Deterministic."""
        where = f'page "{self.page}" of ' if self.page else ""
        tool = {"powerbi": "Power BI"}.get(self.tool, self.tool.capitalize())
        lines = [f'The {self.kind.value.replace("_", " ")} tile "{self.title}" on {where}'
                 f'the {tool} {"report" if self.tool == "powerbi" else "workbook"} "{self.workbook}".']
        shown = [f"{m.label} ({m.agg or 'computed'}: {m.expr})" for m in self.displayed_measures]
        if shown:
            lines.append("It displays: " + "; ".join(shown) + ".")
        if self.dimensions:
            lines.append("Broken down by: " + "; ".join(
                f"{d.label}" + (f" (by {d.bucket})" if d.bucket else "") for d in self.dimensions) + ".")
        else:
            lines.append("No breakdown: a single figure.")
        applied = [f for f in self.filters if f.applied and f.expr]
        if applied:
            lines.append("Filters applied: " + "; ".join(
                (f"{f.label}: " if f.label else "") + f.expr for f in applied) + ".")
        recorded = [f for f in self.filters if not f.applied]
        if recorded:
            lines.append("Filters recorded but not applied (viewer-dependent): " + "; ".join(
                f.label or f.expr or f.kind for f in recorded) + ".")
        if self.source_relation:
            lines.append("Reads: " + ", ".join(self.source_relation) + ".")
        return "\n".join(lines)

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "VisualElement":
        d = dict(d)
        known = {f for f in cls.__dataclass_fields__}
        extra = {k: v for k, v in d.items() if k not in known}
        d = {k: v for k, v in d.items() if k in known}
        try:
            d["kind"] = ElementKind(d.get("kind") or "unknown")
        except ValueError:
            d["kind"] = ElementKind.UNKNOWN
        d["measures"] = [_build(Measure, m) for m in d.get("measures") or []]
        d["dimensions"] = [_build(Dimension, m) for m in d.get("dimensions") or []]
        d["filters"] = [_build(FilterSpec, m) for m in d.get("filters") or []]
        el = cls(**d)
        if extra:
            el.provenance = {**el.provenance, "_unknown_fields": sorted(extra)}
        return el


@dataclass
class MinedElements:
    """What a BI adapter hands the pipeline."""

    candidates: list                            # select.Candidate, each with .element and .sql = element.sql
    refused: list[VisualElement] = field(default_factory=list)
    schema: dict[str, list[dict]] = field(default_factory=dict)   # table -> [{column, type, comment}]
    source: str = "bi"                          # "bi:sigma" | "bi:tableau" | "bi:powerbi"
    note: str = ""


def _build(cls, d: dict):
    """Tolerant constructor: unknown keys are dropped, not fatal, so an older
    reader can load a newer file."""
    known = set(cls.__dataclass_fields__)
    return cls(**{k: v for k, v in d.items() if k in known})


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60]
