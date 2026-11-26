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

from __future__ import annotations

# What the single module imported, kept so its names stay reachable as before (one is re-exported).
import pathlib
import re
from typing import Any
import yaml
from metrics_agent.lib import bi_models as _bi_models
from metrics_agent.lib import declared as _declared
from metrics_agent.lib import slugs
from metrics_agent.lib.card_sql import statement_sha  # noqa: F401 - the review's, re-exported
from metrics_agent.plugs.readers import tableau_params as _tparams
from metrics_agent.sockets import PARTIAL, REFUSED, TRANSLATED, Compiled

# The compiler is split into parts only to keep each file under the Claude plugin directory's
# 256 KiB limit. A part imports only from the parts before it. Every top-level name is
# re-exported here, private ones included, so `bicompile.X` works as it did for one module.
from ._part1 import (  # noqa: F401
    CORPUS,
    CompileError,
    DATA,
    RULES,
    Result,
    TABLE_CALC_NAMES,
    _AGGREGATE_CALL,
    _CARD_ENDS,
    _COMPARISONS,
    _CONTEXT_FREE_FUNCS,
    _CTX_MARK,
    _DATE_LIT,
    _DATE_SETS,
    _DATE_TYPES,
    _DAX_AGGREGATES,
    _DateChain,
    _DateSet,
    _ISO_DAY,
    _LIMIT,
    _LOD_LANGUAGES,
    _LOD_MARK,
    _LOD_OUTER,
    _MODULO,
    _NOT_WALKED,
    _NO_NULLS_LAST,
    _NUM_LIT,
    _NotSimple,
    _PERIOD_TOTALS,
    _READER_DIALECTS,
    _REMOVERS,
    _SIMPLE_IDENT,
    _SortNeedsWindow,
    _SqlDate,
    _TEXT_FN,
    _TOKEN,
    _TimeIntelligence,
    _UnitContext,
    _apply,
    _balanced,
    _bare,
    _blank_passes,
    _brace_end,
    _call_args,
    _call_head,
    _calls_table_calc,
    _card_ends,
    _check_literal_args,
    _check_literal_values,
    _column_ref,
    _constant_count,
    _context_free,
    _date_literal,
    _emit_for,
    _expanded_tokens,
    _filtered_by,
    _foldable,
    _ident,
    _keyed_date,
    _literal,
    _literal_list,
    _lod_refusal,
    _lookup_key,
    _map_aggregates,
    _mod,
    _no_form_reason,
    _part_literal,
    _part_template,
    _quote,
    _reads_rows,
    _scalar_list,
    _sort_term,
    _spec_edge,
    _split_top,
    _sql_string,
    _strip_line_comments,
    _table_ref,
    _tokens,
    _top_colon,
    _year_end,
    dax_year,
    dax_year_sql,
    tokenize,
)
from ._part2 import (  # noqa: F401
    _LOD,
    _Parser,
    _SHELF_LOD,
    _SORT,
    _agg_emit,
    _legal,
    _lods_at_their_grain,
    _measure_alias,
    _native_colmap,
    _over_of,
    _reaches_lod,
    _semi_join,
    _without_literals,
    compile_expression,
)
from ._part3 import (  # noqa: F401
    MEMBER_OPS,
    _ADDITIVE,
    _AGG_SQL,
    _CMP,
    _CONTEXT_TESTS,
    _FOREIGN_AGGREGATES,
    _ITERATORS,
    _JOIN_CLAUSE,
    _JoinPlan,
    _NoTransition,
    _NotConstant,
    _PLAIN_AGGREGATES,
    _REF,
    _RELATIVE_PERIODS,
    _ROW_SCALAR_FUNCS,
    _RowValueUnplaced,
    _SCALAR_WRAPPERS,
    _Step,
    _TEXT_OPS,
    _TRANSITIONS,
    _VIEW_GRAIN_CALL,
    _additive,
    _agg_call,
    _apply_spec,
    _blank_in,
    _blank_member_test,
    _blank_on_empty,
    _blank_rows_having,
    _blend_parts,
    _blend_statement,
    _branch_many_hop,
    _branch_split,
    _columns_of,
    _compile_measure,
    _composite_form,
    _composite_statement,
    _const,
    _constant_item,
    _context_statements,
    _context_tested,
    _date_keys,
    _detached_statement,
    _distributed,
    _edge_key,
    _edge_pairs,
    _empty_context_value,
    _empty_passes,
    _empty_value,
    _expand_branches,
    _filter_reaches,
    _filter_reads_column,
    _grain_statement,
    _hide_blank_rows_after,
    _indent,
    _inline_vars,
    _inner,
    _joined_row_test,
    _labelled,
    _leaf_names,
    _lifted_joins,
    _limit,
    _lod_parts,
    _lod_related_columns,
    _measure_elsewhere,
    _measure_graph,
    _measure_homes,
    _member_filters,
    _member_grain,
    _member_row_values,
    _not_row_level,
    _out_name,
    _padded_crosstab,
    _physical_edges,
    _plan_filters,
    _plan_joins,
    _rank_form,
    _ranked_members,
    _recursive_rows,
    _related,
    _relationship_spec,
    _relative_date,
    _relative_range,
    _renamed_edges,
    _row_tokens,
    _rows_not_on_source,
    _spec_keys,
    _stand_in,
    _statement_form,
    _strip_alias,
    _strip_parens,
    _tables_read,
    _test_pred,
    _tested_members,
    _text_of,
    _top_n_order,
    _treatas_form,
    _treatas_statement,
    _tuple_columns,
    _tuple_pred,
    _untransitioned_aggregate,
    _view_grain_atoms,
    _window_form,
    _with_every_member,
)
from ._part4 import (  # noqa: F401
    FormulaCompiler,
    UNTRANSLATABLE,
    _apply_item,
    _calculation_items,
    _compile_time_unit,
    _compile_totals,
    _compile_unit,
    _date_set_range,
    _date_step,
    _dax_table_name,
    _every_member_rows,
    _formula_home,
    _hoist_recursions,
    _hoisted,
    _item_formats,
    _joined_statements,
    _limited,
    _member_grain_statement,
    _rank_statement,
    _read_statements,
    _shift_bounds,
    _untranslatable,
    _window_statement,
    compile_unit,
)
