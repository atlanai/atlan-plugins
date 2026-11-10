"""Bounded DAX syntax parsing for source inventory types only. No SQL generation."""
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
