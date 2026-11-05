"""Bounded DAX AST -> parameterized warehouse SQL. No evaluation or SQL guessing.

Scalar measures only; one mapped rowset, no implicit relationship expansion.
Unsupported syntax produces a gap, never a plausible-looking replacement query.
"""
import re
from decimal import Decimal


class TranslationGap(ValueError):
    pass


TOKEN = re.compile(r'''\s+|//[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|"(?:[^"]|"")*"|\[(?:[^\]]|\]\])*\]|(?:\d+\.\d+|\d+)|[A-Za-z_][A-Za-z_0-9]*|==|<>|[(),=+\-]''', re.S)


class Parser:
    def __init__(self, expression):
        self.tokens, self.pos, self.variables = [], 0, {}
        end = 0
        for match in TOKEN.finditer(expression):
            if match.start() != end:
                raise TranslationGap('Unsupported DAX syntax; needs semantic authoring.')
            token = match.group(); end = match.end()
            if not token.isspace() and not token.startswith(('//', '/*')):
                self.tokens.append(token)
        if end != len(expression) or len(self.tokens) > 4000:
            raise TranslationGap('Unsupported or oversized DAX expression.')

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ''

    def take(self, expected=None):
        token = self.peek()
        if not token or expected and token.upper() != expected:
            raise TranslationGap('Unsupported DAX expression shape.')
        self.pos += 1
        return token

    def parse(self):
        while self.peek().upper() == 'VAR':
            self.take(); name = self.take()
            if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) or name.upper() in self.variables:
                raise TranslationGap('Unsupported variable declaration.')
            self.take('='); self.variables[name.upper()] = ('var', name.upper(), self.expr())
        if self.variables:
            self.take('RETURN')
        result = self.expr()
        if self.peek():
            raise TranslationGap('Unconsumed DAX syntax; automatic translation refused.')
        return result

    def expr(self, minimum=0):
        left = self.atom()
        precedence = {'=': 1, '==': 1, '<>': 1, '+': 2, '-': 2}
        while self.peek() in precedence and precedence[self.peek()] >= minimum:
            op = self.take(); left = ('op', op, left, self.expr(precedence[op] + 1))
        return left

    def atom(self):
        token = self.take()
        if token == '(':
            value = self.expr(); self.take(')'); return value
        if token.upper() == 'NOT':
            return ('not', self.atom())
        if token == '-':
            value = self.atom()
            if value[0] != 'literal' or type(value[1]) not in (int, float):
                raise TranslationGap('Only numeric literal unary negation is supported.')
            return ('literal', -value[1])
        if token.startswith('"'):
            return ('literal', token[1:-1].replace('""', '"'))
        if re.fullmatch(r'\d+(\.\d+)?', token):
            if '.' in token and (len(Decimal(token).as_tuple().digits) > 15 or Decimal(str(float(token))) != Decimal(token)):
                raise TranslationGap('High-precision decimal literals need a decimal-safe execution adapter.')
            return ('literal', float(token) if '.' in token else int(token))
        if token.startswith('['):
            raise TranslationGap('Measure dependencies require expansion with exact source definitions.')
        name = token[1:-1].replace("''", "'") if token.startswith("'") else token
        if self.peek().startswith('['):
            col = self.take()[1:-1].replace(']]', ']')
            return ('column', name, col)
        if self.peek() == '(':
            self.take(); args = []
            if self.peek() != ')':
                args.append(self.expr())
                while self.peek() == ',':
                    self.take(); args.append(self.expr())
            self.take(')')
            if name.upper() in ('TRUE', 'FALSE', 'BLANK') and not args:
                return ('literal', {'TRUE': True, 'FALSE': False, 'BLANK': None}[name.upper()])
            return ('call', name.upper(), args)
        if name.upper() in self.variables:
            return self.variables[name.upper()]
        return ('table', name)


def identifier(value):
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise TranslationGap('A physical identifier is invalid.')
    return '"' + value.replace('"', '""') + '"'


class Compiler:
    def __init__(self, mapping, context, dialect):
        if dialect not in ('snowflake', 'postgres', 'sqlite'):
            raise TranslationGap('This translator supports Snowflake, Postgres and SQLite SQL.')
        if not mapping.get('rowset_equivalent') or not mapping.get('security_preserved'):
            raise TranslationGap('Resolve source transformations, row grain and row-level security before SQL translation.')
        self.table = mapping['semantic_table']
        self.relation = '.'.join(identifier(p) for p in mapping['relation'])
        self.source_sql = mapping.get('rowset_sql')
        self.from_sql = self.relation
        if self.source_sql:
            from .snowflake_cli import bound_query, CLIError
            try:
                bound_query(self.source_sql, {})
            except CLIError as exc:
                raise TranslationGap(str(exc)) from None
            self.source_sql = self.source_sql.strip().rstrip(';')
            self.from_sql = '(' + self.source_sql + ')'
        self.columns = {}
        for col in mapping['columns']:
            if col['semantic'] in self.columns:
                raise TranslationGap('Duplicate semantic column mapping.')
            self.columns[col['semantic']] = col
        self.params, self.dialect = {}, dialect
        self.context = list(context.get('filters', []))
        self.security = list(mapping.get('mandatory_filters', []))
        self.warnings = ['Source rowset/security equivalence is host-declared; verify with warehouse results.',
                         'Text grouping/collation and synthetic relationship blank rows require source-equivalent mapping.']

    def bind(self, value):
        name = 'p' + str(len(self.params)); self.params[name] = value
        return ':' + name

    def column(self, ast):
        if ast[0] != 'column' or ast[1] != self.table or ast[2] not in self.columns:
            raise TranslationGap('Missing exact physical column mapping or cross-table semantics.')
        col = self.columns[ast[2]]
        return 's.' + identifier(col['physical'])

    def predicate(self, node):
        if node[0] == 'not' and node[1][0:2] == ('call', 'ISBLANK') and len(node[1][2]) == 1:
            col = node[1][2][0]
            return col[2], self.column(col) + ' IS NOT NULL'
        if node[0:2] == ('call', 'ISBLANK') and len(node[2]) == 1:
            col = node[2][0]
            return col[2], self.column(col) + ' IS NULL'
        if node[0:2] == ('call', 'CONTAINSSTRING') and len(node[2]) == 2:
            col, literal = node[2]
            if literal[0] != 'literal' or not isinstance(literal[1], str) or not literal[1].isascii() or any(c in literal[1] for c in '*?~'):
                raise TranslationGap('CONTAINSSTRING wildcards or non-ASCII collation need reviewed translation.')
            value = self.bind(literal[1].upper())
            fn = 'POSITION(' + value + ' IN UPPER(COALESCE(' + self.column(col) + ", '')))" if self.dialect == 'postgres' else 'INSTR(UPPER(COALESCE(' + self.column(col) + ", '')), " + value + ')'
            # Snowflake uses POSITION, SQLite uses INSTR.
            if self.dialect == 'snowflake':
                fn = 'POSITION(' + value + ', UPPER(COALESCE(' + self.column(col) + ", '')))"
            return col[2], fn + ' > 0'
        if node[0] == 'op' and node[1] in ('=', '==') and node[2][0] == 'column' and node[3][0] == 'literal':
            col, val = node[2], node[3][1]; ref = self.column(col)
            if val is None:
                if node[1] != '==':
                    raise TranslationGap('Loose equality to BLANK needs datatype-specific coercion; use source-grounded authoring.')
                return col[2], ref + ' IS NULL'
            literal = self.bind(val)
            if isinstance(val, str):
                if not val.isascii():
                    raise TranslationGap('Non-ASCII DAX text comparison needs source collation validation.')
                result = 'UPPER(' + ref + ') = UPPER(' + literal + ')'
            else:
                result = ref + ' = ' + literal
            if node[1] == '=' and (val is False or type(val) in (int, float) and val == 0 or val == ''):
                result = '(' + result + ' OR ' + ref + ' IS NULL)'
            return col[2], result
        raise TranslationGap('Unsupported CALCULATE predicate; do not approximate its filter semantics.')

    def context_filter(self, item):
        col = ('column', item['table'], item['column']); ref = self.column(col)
        values = item['values']
        pieces = []
        for value in values:
            if value is None:
                pieces.append(ref + ' IS NULL')
            elif isinstance(value, str):
                if not value.isascii():
                    raise TranslationGap('Non-ASCII filter values need source collation validation.')
                pieces.append('UPPER(' + ref + ') = UPPER(' + self.bind(value) + ')')
            else:
                pieces.append(ref + ' = ' + self.bind(value))
        return item['column'], '(' + ' OR '.join(pieces) + ')' if pieces else '1=0'

    def aggregate(self, name, args, context):
        if len(args) != 1:
            raise TranslationGap('Unsupported aggregate argument count.')
        if name == 'COUNTROWS':
            if args[0] != ('table', self.table):
                raise TranslationGap('COUNTROWS needs the exact mapped semantic table.')
            formula = 'NULLIF(COUNT(*), 0)'
        else:
            ref = self.column(args[0])
            kind = self.columns[args[0][2]]['datatype']
            if name in ('SUM', 'AVERAGE') and kind not in ('integer', 'decimal'):
                raise TranslationGap('Numeric aggregation requires a numeric physical column.')
            if name in ('MIN', 'MAX', 'SELECTEDVALUE') and kind == 'boolean':
                raise TranslationGap('Boolean value aggregation requires dialect-specific authoring.')
            unique = '(COUNT(DISTINCT ' + ref + ') + CASE WHEN COUNT(*) > COUNT(' + ref + ') THEN 1 ELSE 0 END)'
            if name == 'DISTINCTCOUNT':
                formula = 'CASE WHEN COUNT(*) = 0 THEN NULL ELSE ' + unique + ' END'
            elif name == 'SELECTEDVALUE':
                formula = 'CASE WHEN ' + unique + ' = 1 THEN MIN(' + ref + ') ELSE NULL END'
            elif name in ('SUM', 'AVERAGE', 'MIN', 'MAX'):
                formula = {'AVERAGE': 'AVG'}.get(name, name) + '(' + ref + ')'
            else:
                raise TranslationGap('Unsupported aggregate; needs semantic authoring.')
        predicates = [sql for _, sql in context]
        # These cannot be overwritten by CALCULATE; security survives context replacement.
        predicates += [self.context_filter(f)[1] for f in self.security]
        where = '\nWHERE ' + ' AND '.join('(' + p + ')' for p in predicates) if predicates else ''
        return '(SELECT ' + formula + '\nFROM ' + self.from_sql + ' AS s' + where + ')'

    def scalar(self, ast, context):
        if ast[0] == 'var':
            # Top-level VAR values are fixed before CALCULATE changes the context.
            return self.scalar(ast[2], self.root_context)
        if ast[0] == 'literal':
            return 'NULL' if ast[1] is None else self.bind(ast[1])
        if ast[0] == 'op' and ast[1] in ('+', '-'):
            left, right = ast[2:]
            if ast[1] == '+' and right == ('literal', 0):
                return 'COALESCE(' + self.scalar(left, context) + ', 0)'
            a, b = self.scalar(left, context), self.scalar(right, context)
            return '(CASE WHEN ' + a + ' IS NULL AND ' + b + ' IS NULL THEN NULL ELSE COALESCE(' + a + ', 0) ' + ast[1] + ' COALESCE(' + b + ', 0) END)'
        if ast[0] != 'call':
            raise TranslationGap('Unsupported scalar expression.')
        name, args = ast[1:]
        if name == 'CALCULATE' and args:
            predicates, replace = [], set()
            for predicate in args[1:]:
                keep = predicate[0:2] == ('call', 'KEEPFILTERS')
                if keep:
                    if len(predicate[2]) != 1:
                        raise TranslationGap('Unsupported KEEPFILTERS expression.')
                    predicate = predicate[2][0]
                col, sql = self.predicate(predicate)
                predicates.append((col, sql))
                if not keep: replace.add(col)
            return self.scalar(args[0], [f for f in context if f[0] not in replace] + predicates)
        return self.aggregate(name, args, context)

    def compile(self, expression):
        ast = Parser(expression).parse()
        context = [self.context_filter(item) for item in self.context]
        self.root_context = context
        sql = 'SELECT ' + self.scalar(ast, context) + ' AS "value"'
        # CALCULATE can remove already-bound context predicates; remove unused parameters.
        used = set(re.findall(r':(p\d+)\b', sql))
        parameters = {name: value for name, value in self.params.items() if name in used}
        return {'sql': sql, 'parameters': parameters, 'binding_style': 'named_colon', 'dialect': self.dialect,
                'relation': self.relation, 'source_sql': self.source_sql, 'warnings': self.warnings, 'compiler': 'dax-scalar-ast/1'}


def translate(expression, mapping, context, dialect):
    try:
        return Compiler(mapping, context, dialect).compile(expression)
    except (RecursionError, IndexError, KeyError):
        raise TranslationGap('Expression or mapping is outside the bounded translator contract.') from None
