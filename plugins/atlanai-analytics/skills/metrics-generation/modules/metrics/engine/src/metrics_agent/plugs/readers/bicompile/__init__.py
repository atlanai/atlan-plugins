# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Atlan Pte. Ltd.
"""The BI→SQL compiler the built-in readers share. Deterministic, rule-driven, and the primary
path at A3.

IT IS THE READERS', NOT THE CORE'S (AGENT_GUARDRAILS rule 22). A formula is written in a BI
tool's language, so turning it into SQL is the reader's job. The `tableau` and `powerbi`
readers both compile through this module (`FormulaCompiler`, at the bottom): A3 reaches it only
as the reader's `prepare` and `compile_unit` (`sockets.BIReader`), and a new reader brings its
own compiler or none. The core keeps only what every compiler shares: the rule-table format and
its gate (`lib/rules.py`), and the `Compiled` result (`sockets.Compiled`).

A3 used to be an LLM stage: the model read the widget's facts and wrote the SQL. That is
expensive, irreproducible, and — worst — its mistakes are invisible, because fluent SQL that
computes the wrong thing looks exactly like fluent SQL that computes the right thing.

THE INVERSION. The script writes the SQL; the model REVIEWS it. What the script knows lives in
`bi_compiler_rules.yml` as DATA, so a review pass can propose rows and the run can promote them
without anybody editing Python. A row is schema-checkable, diffable and reversible; a code patch
is none of those.

WHAT THIS FILE OWNS, and what it never will:

  owns    the SHAPE of the statement — which table is the source, which joins are needed to
          reach a grouping column, where each filter goes, what order things evaluate in.
  rules   the VOCABULARY — what `DIVIDE` becomes, what `COUNTD` becomes, which constructs have
          no SQL at all.

Shape is judgement and belongs in reviewed code. Vocabulary is a lookup table and belongs in
data. Confusing the two is how a compiler becomes a pile of string replacement.

TABLEAU'S ORDER OF OPERATIONS IS A CORRECTNESS TRAP, NOT A SYNTAX ONE. A context filter narrows
the population BEFORE a Top-N or an LOD is computed. Flattening it into the same WHERE produces
SQL that runs, passes the witness, and returns a different number — so context filters get their
own scope and the boundary is structural, not stylistic.

UNTRANSLATABLE IS A REAL ANSWER. A table calculation runs after the query on the result set; a
FIXED LOD computes at a grain the statement does not have. The compiler NAMES the construct and
declares the unit partial. A rule that pretended otherwise would be the worst kind of defect:
one that runs.
"""

# The compiler is a package only to keep each file under the Claude plugin directory's 256 KiB
# limit. Each module holds one part of the job (see its docstring). Every top-level name is
# re-exported here, private ones included, so `bicompile.X` works as it did for one module.
from __future__ import annotations

from metrics_agent.lib.card_sql import statement_sha  # noqa: F401 - the review's, re-exported
from .common import (  # noqa: F401
    CORPUS,
    CompileError,
    DATA,
    RULES,
    Result,
    TABLE_CALC_NAMES,
    _SortNeedsWindow,
    _TOKEN,
    _calls_table_calc,
    _constant_count,
)
from .parser import (  # noqa: F401
    _Parser,
    _TEXT_FN,
    _balanced,
    _column_ref,
    _quote,
    _sql_string,
    _strip_line_comments,
    _table_ref,
    tokenize,
)
from .expressions import (  # noqa: F401
    _AGGREGATE_CALL,
    _COMPARISONS,
    _CTX_MARK,
    _DATE_SETS,
    _DateChain,
    _DateSet,
    _ISO_DAY,
    _LOD,
    _LOD_LANGUAGES,
    _LOD_MARK,
    _LOD_OUTER,
    _NotSimple,
    _PERIOD_TOTALS,
    _REMOVERS,
    _SqlDate,
    _TimeIntelligence,
    _UnitContext,
    _agg_emit,
    _apply,
    _blank_passes,
    _brace_end,
    _call_args,
    _call_head,
    _check_literal_args,
    _check_literal_values,
    _date_literal,
    _emit_for,
    _lod_refusal,
    _map_aggregates,
    _no_form_reason,
    _part_literal,
    _part_template,
    _reads_rows,
    _scalar_list,
    _semi_join,
    _split_top,
    _tokens,
    _top_colon,
    _without_literals,
    _year_end,
    compile_expression,
    dax_year,
    dax_year_sql,
)
from .statements import (  # noqa: F401
    _ADDITIVE,
    _CONTEXT_FREE_FUNCS,
    _CONTEXT_TESTS,
    _DAX_AGGREGATES,
    _FOREIGN_AGGREGATES,
    _ITERATORS,
    _NoTransition,
    _PLAIN_AGGREGATES,
    _ROW_SCALAR_FUNCS,
    _RowValueUnplaced,
    _SCALAR_WRAPPERS,
    _SIMPLE_IDENT,
    _TRANSITIONS,
    _additive,
    _blend_parts,
    _blend_statement,
    _compile_unit,
    _composite_form,
    _composite_statement,
    _constant_item,
    _context_free,
    _context_tested,
    _dax_table_name,
    _detached_statement,
    _distributed,
    _every_member_rows,
    _filter_reaches,
    _filtered_by,
    _formula_home,
    _hoist_recursions,
    _ident,
    _inline_vars,
    _joined_statements,
    _labelled,
    _leaf_names,
    _legal,
    _lifted_joins,
    _literal,
    _literal_list,
    _measure_alias,
    _member_grain,
    _member_grain_statement,
    _member_row_values,
    _native_colmap,
    _not_row_level,
    _out_name,
    _rank_form,
    _rank_statement,
    _recursive_rows,
    _row_tokens,
    _statement_form,
    _strip_parens,
    _tables_read,
    _text_of,
    _treatas_form,
    _treatas_statement,
    _untransitioned_aggregate,
    _window_form,
    _window_statement,
)
from .units import (  # noqa: F401
    _NotConstant,
    _SHELF_LOD,
    _SORT,
    _VIEW_GRAIN_CALL,
    _agg_call,
    _blank_on_empty,
    _compile_measure,
    _compile_time_unit,
    _compile_totals,
    _const,
    _context_statements,
    _date_set_range,
    _date_step,
    _empty_context_value,
    _empty_value,
    _grain_statement,
    _hoisted,
    _indent,
    _inner,
    _limited,
    _lod_parts,
    _lod_related_columns,
    _lods_at_their_grain,
    _over_of,
    _padded_crosstab,
    _reaches_lod,
    _read_statements,
    _related,
    _rows_not_on_source,
    _shift_bounds,
    _top_n_order,
    _view_grain_atoms,
    compile_unit,
)
from .joins import (  # noqa: F401
    MEMBER_OPS,
    _AGG_SQL,
    _CARD_ENDS,
    _CMP,
    _DATE_LIT,
    _DATE_TYPES,
    _JOIN_CLAUSE,
    _JoinPlan,
    _LIMIT,
    _MODULO,
    _NOT_WALKED,
    _NO_NULLS_LAST,
    _NUM_LIT,
    _READER_DIALECTS,
    _REF,
    _RELATIVE_PERIODS,
    _Step,
    _TEXT_OPS,
    _apply_spec,
    _bare,
    _blank_in,
    _blank_member_test,
    _blank_rows_having,
    _branch_many_hop,
    _branch_split,
    _card_ends,
    _columns_of,
    _date_keys,
    _edge_key,
    _edge_pairs,
    _empty_passes,
    _expand_branches,
    _expanded_tokens,
    _filter_reads_column,
    _foldable,
    _hide_blank_rows_after,
    _joined_row_test,
    _keyed_date,
    _limit,
    _lookup_key,
    _measure_elsewhere,
    _measure_graph,
    _measure_homes,
    _member_filters,
    _mod,
    _physical_edges,
    _plan_filters,
    _plan_joins,
    _ranked_members,
    _relationship_spec,
    _relative_date,
    _relative_range,
    _renamed_edges,
    _sort_term,
    _spec_edge,
    _spec_keys,
    _stand_in,
    _strip_alias,
    _test_pred,
    _tested_members,
    _tuple_columns,
    _tuple_pred,
    _with_every_member,
)
from .reader_hooks import (  # noqa: F401
    FormulaCompiler,
    UNTRANSLATABLE,
    _apply_item,
    _calculation_items,
    _item_formats,
    _untranslatable,
)
