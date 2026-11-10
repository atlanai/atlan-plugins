"""Prove required member scope from common BI units and the exported SQL.

No vendor names or review prose participate. A captured member selection must constrain the
SQL, or an explicitly declared per-member variant must group on that member's source key.
Unproved scope stays held; a parser success alone is not a population proof.
"""


def required_filter_holds(queries, units):
    bi = [q for q in queries['objects'] if (q.get('provenance') or {}).get('lane') in ('bi', 'merged')]
    if not bi:
        return {}
    if units is None:
        raise ValueError('BI publication requires the completed source-unit scope evidence.')
    indexed = {u['unit_id']: u for u in units['units']}
    if len(indexed) != len(units['units']):
        raise ValueError('Source-unit scope evidence has duplicate identities.')
    held = {}
    for query in bi:
        unit_id = ((query.get('provenance') or {}).get('bi') or {}).get('unit_id')
        if unit_id not in indexed:
            raise ValueError('A BI query has no matching source-unit scope evidence.')
        unit = indexed[unit_id]
        required = [p for p in unit.get('parameters') or [] if p['kind'] == 'member']
        if not required:
            continue
        missing = _unproved(query['sql'], queries['dialect'], unit, required)
        if missing:
            held[query['vq_id']] = {'reason': 'required_filter_missing',
                'detail': 'Required member scope is not proven in the SQL for: ' + ', '.join(missing)}
    return held


def _unproved(sql, dialect, unit, required):
    # An uncaptured member cannot be chosen by inventing a SQL literal. This metadata-only
    # refusal also needs no parser when the original single-member view has no selection.
    def selected(parameter):
        for f in unit['filters']:
            if str(f.get('column', '')).casefold() != parameter['column'].casefold() or f.get('value_kind') == 'absent':
                continue
            op, value = str(f.get('op', '')).lower(), f.get('value')
            if (op in ('eq', '=') and value is not None or op == 'in' and isinstance(value, list) and value
                    or op == 'is' and value is None):
                return True
        return False
    absent = [p['column'] for p in required if not selected(p)]
    if absent and not unit.get('variant_of'):
        return absent

    # These dependencies already belong to the engine. Host publication preflights need
    # only the generated hold manifest and never import a SQL parser.
    from decimal import Decimal, InvalidOperation
    from metrics_agent.lib import sqlshape
    from sqlglot import exp, parse_one
    from sqlglot.optimizer.scope import build_scope

    def norm(value):
        return str(value).strip('"`[]').casefold()

    def table_key(value):
        if isinstance(value, exp.Table):
            return tuple(norm(p.name) for p in value.parts)
        if not value:
            return None
        try:
            return table_key(parse_one(str(value), read=dialect, into=exp.Table))
        except Exception:
            return None

    estate = [table_key(t) for t in unit['warehouse_tables']]

    def matches(actual, target):
        if not actual or not target:
            return False
        if actual == target:
            return True
        short, long = sorted((actual, target), key=len)
        # A partially qualified name is evidence only when the declared estate resolves it
        # to exactly one table. A same-named column on a different joined table is no proof.
        choices = {t for t in estate if t and t[-len(short):] == short}
        return long[-len(short):] == short and len(choices) == 1 and target in choices

    def source_column(source, name, seen):
        if isinstance(source, exp.Table):
            return table_key(source), norm(name)
        key = (id(source), norm(name))
        if key in seen or not isinstance(source.expression, exp.Select):
            return None
        seen = seen | {key}
        projections = source.expression.expressions
        outer = getattr(source, 'outer_columns', []) or []
        if outer:
            positions = [i for i, c in enumerate(outer) if norm(c) == norm(name)]
            selected = [projections[i] for i in positions if i < len(projections)]
        else:
            selected = [p for p in projections if norm(p.alias_or_name) == norm(name)]
        if len(selected) == 1:
            column = selected[0].this if isinstance(selected[0], exp.Alias) else selected[0]
            return origin(column, source, seen) if isinstance(column, exp.Column) else None
        if not selected and len(projections) == 1 and projections[0].is_star:
            return origin(exp.column(name), source, seen)
        return None

    def origin(column, scope, seen=frozenset()):
        if not isinstance(column, exp.Column) or column.is_star:
            return None
        sources = {norm(alias): source for alias, (_, source) in scope.selected_sources.items()}
        source = sources.get(norm(column.table)) if column.table else next(iter(sources.values())) if len(sources) == 1 else None
        if source is None:
            return None
        result = source_column(source, column.name, seen)
        # Repeated source aliases (including a self-joined CTE) cannot establish which
        # occurrence the unit's unaliased physical member declaration intended.
        if result and sum(source_column(s, column.name, seen) == result for s in sources.values()) > 1:
            return None
        return result

    def value(node):
        if isinstance(node, exp.Null):
            return ('null', None)
        if isinstance(node, exp.Boolean):
            return ('boolean', bool(node.this))
        if isinstance(node, exp.Literal):
            if node.is_string:
                return ('text', node.this)
            try:
                number = Decimal(node.this)
                return ('number', number) if number.is_finite() else None
            except InvalidOperation:
                return None
        if isinstance(node, exp.Neg) and isinstance(node.this, exp.Literal):
            inner = value(node.this)
            return ('number', -inner[1]) if inner and inner[0] == 'number' else None
        return None

    def captured(value):
        if value is None:
            return ('null', None)
        if type(value) is bool:
            return ('boolean', value)
        if isinstance(value, str):
            return ('text', value)
        if type(value) in (int, float):
            number = Decimal(str(value))
            return ('number', number) if number.is_finite() else None
        return None

    def conjuncts(node):
        while isinstance(node, exp.Paren):
            node = node.this
        return conjuncts(node.this) + conjuncts(node.expression) if isinstance(node, exp.And) else [node]

    def selection(node, scope):
        if isinstance(node, exp.EQ):
            left, right = node.this, node.expression
            if not isinstance(left, exp.Column):
                left, right = right, left
            v = value(right)
            # SQL equality with NULL never binds a blank member.
            return (origin(left, scope), {v}) if v and v[0] != 'null' else (None, None)
        if isinstance(node, exp.In) and not node.args.get('query'):
            values = [value(v) for v in node.expressions]
            if values and all(v and v[0] != 'null' for v in values):
                return origin(node.this, scope), set(values)
        if isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
            return origin(node.this, scope), {('null', None)}
        return None, None

    try:
        tree = sqlshape.parse(sql, dialect)
        scope = build_scope(tree)
        if scope is None or not isinstance(scope.expression, exp.Select):
            return [p['column'] for p in required]
        where = scope.expression.args.get('where')
        restrictions = [selection(n, scope) for n in conjuncts(where.this)] if where else []
        group = scope.expression.args.get('group')
        grouped = [origin(g, scope) for g in group.expressions] if group else []
        missing = []
        for parameter in required:
            target = table_key(parameter.get('table')) or (estate[0] if len(estate) == 1 else None)
            column = norm(parameter['column'])
            def member(ref):
                return ref is not None and ref[1] == column and matches(ref[0], target)
            # Only the reader's explicitly declared variant changes the question to every
            # member. Grouping the original single-member view does not bind its selection.
            if unit.get('variant_of') and column in {norm(c) for c in unit['group_by']} and any(member(g) for g in grouped):
                continue
            expected = []
            for f in unit['filters']:
                if norm(f.get('column', '')) != column or f.get('value_kind') == 'absent':
                    continue
                ft = table_key(f.get('table')) or (estate[0] if len(estate) == 1 else None)
                if not matches(ft, target):
                    continue
                op = str(f.get('op', '')).lower()
                raw = f.get('value')
                if op in ('eq', '=') and raw is not None:
                    values = [captured(raw)]
                elif op == 'in' and isinstance(raw, list) and raw:
                    values = [captured(v) for v in raw]
                elif op == 'is' and raw is None:
                    values = [('null', None)]
                else:
                    continue
                if all(v is not None for v in values):
                    expected.append(set(values))
            if not any(member(ref) and values in expected for ref, values in restrictions):
                missing.append(parameter['column'])
        return missing
    except Exception:
        # Unsupported lineage or SQL syntax is unproved scope, never permission to publish.
        return [p['column'] for p in required]
