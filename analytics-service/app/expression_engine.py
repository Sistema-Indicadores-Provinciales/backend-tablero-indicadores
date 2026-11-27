"""Bounded expression language. Cached syntax trees contain no source data."""
from dataclasses import dataclass
from functools import lru_cache
import math
import re

import numpy as np
import pandas as pd


class ExpressionError(ValueError):
    pass


@dataclass(frozen=True)
class Term:
    operation: str
    value: object = None
    children: tuple = ()


AGGREGATES = {'SUM', 'AVG', 'MIN', 'MAX', 'MEDIAN', 'COUNT', 'COUNT_DISTINCT'}
ARITIES = {**{name: (1, 1) for name in AGGREGATES}, 'IF': (3, 3),
           'NULLIF': (2, 2), 'COALESCE': (2, 20), 'ROUND': (1, 2),
           'CONCAT': (1, 20), 'SELECTED_COUNT': (1, 1)}
LEXEME = re.compile(r'''\s*(?:(\[(?:[^\]]|\]\])*\])|("(?:[^"\\]|\\.|"")*"|'(?:[^'\\]|\\.|'')*')|((?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|([A-Za-z_][A-Za-z_0-9]*)|(<=|>=|!=|<>|[=<>+*/(),-]))''')
PRECEDENCE = {'OR': 1, 'AND': 2, '=': 3, '!=': 3, '<>': 3, '<': 3,
              '<=': 3, '>': 3, '>=': 3, 'IS': 3, '+': 4, '-': 4, '*': 5, '/': 5}


class Parser:
    def __init__(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ExpressionError('La expresión debe contener entre 1 y 2.000 caracteres.')
        self.tokens, self.position = [], 0
        offset = 0
        while offset < len(text.rstrip()):
            match = LEXEME.match(text, offset)
            if not match:
                raise ExpressionError(f'Símbolo no admitido en la posición {offset + 1}.')
            field, string, number, word, symbol = match.groups()
            if number and not math.isfinite(float(number)):
                raise ExpressionError('El literal numérico debe ser finito.')
            self.tokens.append(('field', field[1:-1].replace(']]', ']')) if field else
                               ('literal', self.string(string)) if string else
                               ('literal', float(number)) if number else
                               ('word', word.upper()) if word else ('symbol', symbol))
            offset = match.end()
        if len(self.tokens) > 256:
            raise ExpressionError('La expresión supera 256 elementos.')

    @staticmethod
    def string(text):
        quote = text[0]
        return re.sub(r'\\([\\\'"nrt])', lambda m: {'n': '\n', 'r': '\r', 't': '\t'}.get(m[1], m[1]),
                      text[1:-1].replace(quote + quote, quote))

    def peek(self):
        return self.tokens[self.position][1] if self.position < len(self.tokens) else None

    def take(self, expected=None):
        if self.position >= len(self.tokens):
            raise ExpressionError('La expresión está incompleta.')
        token = self.tokens[self.position]
        if expected is not None and token[1] != expected:
            raise ExpressionError(f'Se esperaba {expected}.')
        self.position += 1
        return token

    def expression(self, minimum=0, depth=0):
        if depth > 40:
            raise ExpressionError('La expresión tiene demasiados niveles.')
        kind, value = self.take()
        if kind in {'literal', 'field'}:
            left = Term(kind, value)
        elif value == '(':
            left = self.expression(depth=depth + 1)
            self.take(')')
        elif value in {'+', '-', 'NOT'}:
            left = Term('unary', value, (self.expression(3 if value == 'NOT' else 6, depth + 1),))
        elif value in {'NULL', 'TRUE', 'FALSE'}:
            left = Term('literal', {'NULL': None, 'TRUE': True, 'FALSE': False}[value])
        elif value == 'CASE':
            branches = []
            while self.peek() == 'WHEN':
                self.take('WHEN')
                condition = self.expression(depth=depth + 1)
                self.take('THEN')
                branches.extend((condition, self.expression(depth=depth + 1)))
            if not branches:
                raise ExpressionError('CASE necesita al menos una condición WHEN.')
            default = Term('literal')
            if self.peek() == 'ELSE':
                self.take('ELSE')
                default = self.expression(depth=depth + 1)
            self.take('END')
            left = Term('case', children=tuple([*branches, default]))
        elif kind == 'word' and value in ARITIES:
            self.take('(')
            arguments = [self.expression(depth=depth + 1)]
            while self.peek() == ',':
                self.take(',')
                arguments.append(self.expression(depth=depth + 1))
            self.take(')')
            low, high = ARITIES[value]
            if not low <= len(arguments) <= high:
                raise ExpressionError(f'{value} necesita entre {low} y {high} argumentos.')
            left = Term('call', value, tuple(arguments))
        else:
            raise ExpressionError(f'Expresión no admitida: {value}. Usá corchetes para los campos.')
        while self.peek() in PRECEDENCE and PRECEDENCE[self.peek()] >= minimum:
            operator = self.take()[1]
            if operator == 'IS':
                negated = self.peek() == 'NOT'
                if negated:
                    self.take('NOT')
                self.take('NULL')
                left = Term('null', negated, (left,))
            else:
                right = self.expression(PRECEDENCE[operator] + 1, depth + 1)
                left = Term('binary', operator, (left, right))
        return left


@lru_cache(maxsize=512)
def compile_expression(expression):
    parser = Parser(expression)
    term = parser.expression()
    if parser.position != len(parser.tokens):
        raise ExpressionError('Hay contenido adicional después de la expresión.')
    return term


def missing(value):
    return value.isna() if isinstance(value, pd.Series) else value is None or bool(pd.isna(value))


def numeric(value):
    if value is None:
        return np.nan
    if isinstance(value, pd.Series) and pd.api.types.is_numeric_dtype(value.dtype) and not pd.api.types.is_bool_dtype(value.dtype):
        return value
    values = value.dropna().tolist() if isinstance(value, pd.Series) else [value]
    if any(not isinstance(item, (int, float, np.number)) or isinstance(item, (bool, np.bool_)) for item in values):
        raise ExpressionError('La operación requiere valores numéricos. Revisá los tipos de las columnas.')
    return pd.to_numeric(value) if isinstance(value, pd.Series) else value


def boolean(value):
    if isinstance(value, pd.Series) and pd.api.types.is_bool_dtype(value.dtype):
        return value.fillna(False).astype(bool)
    values = value.dropna().tolist() if isinstance(value, pd.Series) else [] if missing(value) else [value]
    if any(not isinstance(item, (bool, np.bool_)) for item in values):
        raise ExpressionError('La condición requiere una comparación o un resultado lógico.')
    return value.fillna(False).astype(bool) if isinstance(value, pd.Series) else False if missing(value) else bool(value)


def compatible(left, right):
    def category(value):
        values = value.dropna().tolist() if isinstance(value, pd.Series) else [] if missing(value) else [value]
        return {'number' if isinstance(item, (int, float, np.number)) and not isinstance(item, (bool, np.bool_))
                else 'boolean' if isinstance(item, (bool, np.bool_)) else 'text' if isinstance(item, str) else 'other' for item in values}
    kinds = category(left) | category(right)
    if len(kinds) > 1:
        raise ExpressionError('No se pueden combinar o comparar resultados de tipos diferentes.')


def choose(condition, yes, no, index):
    compatible(yes, no)
    condition = boolean(condition)
    if not isinstance(condition, pd.Series):
        return yes if condition else no
    first = yes if isinstance(yes, pd.Series) else pd.Series(yes, index=index)
    return first.where(condition, no)


def evaluate(term, frame, selected=None):
    op, value, children = term.operation, term.value, term.children
    if op == 'literal':
        return value
    if op == 'field':
        if value not in frame:
            raise ExpressionError(f'No existe el campo [{value}].')
        return frame[value]
    if op == 'call' and value == 'SELECTED_COUNT':
        argument = children[0]
        if argument.operation not in {'field', 'literal'} or not isinstance(argument.value, str):
            raise ExpressionError('SELECTED_COUNT necesita el nombre de un filtro.')
        if argument.value not in frame and argument.value not in (selected or {}):
            raise ExpressionError(f'No existe el campo o filtro [{argument.value}].')
        selection = (selected or {}).get(argument.value, [])
        if isinstance(selection, dict):
            selection = selection.get('include', [])
        return len(set(selection))
    args = [evaluate(child, frame, selected) for child in children]
    if op == 'null':
        mask = missing(args[0])
        return ~mask if value and isinstance(mask, pd.Series) else not mask if value else mask
    if op == 'unary':
        if value == 'NOT':
            result = boolean(args[0])
            return ~result if isinstance(result, pd.Series) else not result
        return numeric(args[0]) * (-1 if value == '-' else 1)
    if op == 'binary':
        left, right = args
        if value in {'AND', 'OR'}:
            left, right = boolean(left), boolean(right)
            return left & right if value == 'AND' else left | right
        if value in {'+', '-', '*', '/'}:
            left, right = numeric(left), numeric(right)
            with np.errstate(all='ignore'):
                if value == '/':
                    right = right.mask(right.eq(0)) if isinstance(right, pd.Series) else np.nan if right == 0 else right
                result = {'+': lambda: left + right, '-': lambda: left - right, '*': lambda: left * right, '/': lambda: left / right}[value]()
            return result.replace([np.inf, -np.inf], np.nan) if isinstance(result, pd.Series) else result if math.isfinite(result) else None
        compatible(left, right)
        absent = missing(left) | missing(right)
        if not isinstance(left, pd.Series) and missing(left):
            return pd.Series(False, index=right.index) if isinstance(right, pd.Series) else False
        if not isinstance(right, pd.Series) and missing(right):
            return pd.Series(False, index=left.index) if isinstance(left, pd.Series) else False
        if not isinstance(absent, pd.Series) and absent:
            return False
        # Pandas keeps nullable comparisons vectorized; NULL never compares equal.
        try:
            result = {'=': lambda: left == right, '!=': lambda: left != right, '<>': lambda: left != right,
                      '<': lambda: left < right, '<=': lambda: left <= right, '>': lambda: left > right, '>=': lambda: left >= right}[value]()
        except TypeError as exc:
            raise ExpressionError('La comparación usa tipos incompatibles.') from exc
        return result & ~absent if isinstance(absent, pd.Series) else result
    if op == 'case':
        result = args[-1]
        for index in range(len(args) - 3, -1, -2):
            result = choose(args[index], args[index + 1], result, frame.index)
        return result
    if value in AGGREGATES:
        series = args[0] if isinstance(args[0], pd.Series) else pd.Series(args[0], index=frame.index)
        if value == 'COUNT':
            return int(series.count())
        if value == 'COUNT_DISTINCT':
            return int(series.nunique())
        series = numeric(series)
        if not series.notna().any():
            return None
        result = getattr(series, {'SUM': 'sum', 'AVG': 'mean', 'MIN': 'min', 'MAX': 'max', 'MEDIAN': 'median'}[value])()
        return result if math.isfinite(result) else None
    if value == 'IF':
        return choose(*args, frame.index)
    if value == 'COALESCE':
        result = args[-1]
        for argument in reversed(args[:-1]):
            result = choose(~missing(argument) if isinstance(argument, pd.Series) else not missing(argument), argument, result, frame.index)
        return result
    if value == 'NULLIF':
        compatible(*args)
        first, second = args
        if isinstance(first, pd.Series):
            return first.mask(first.eq(second))
        return None if first == second else first
    if value == 'ROUND':
        digits = args[1] if len(args) > 1 else 0
        if isinstance(digits, pd.Series) or not isinstance(digits, (int, float)) or int(digits) != digits or not -10 <= digits <= 10:
            raise ExpressionError('ROUND admite una cantidad entera entre -10 y 10 decimales.')
        return np.round(numeric(args[0]), int(digits))
    if value == 'CONCAT':
        def text(argument):
            if isinstance(argument, pd.Series):
                return argument.map(lambda item: '' if missing(item) else str(item))
            return '' if missing(argument) else str(argument)
        result = text(args[0])
        for argument in args[1:]:
            result = result + text(argument)
            if (result.str.len().gt(10000).any() if isinstance(result, pd.Series) else len(result) > 10000):
                raise ExpressionError('CONCAT supera 10.000 caracteres por resultado.')
        if (result.str.len().gt(10000).any() if isinstance(result, pd.Series) else len(result) > 10000):
            raise ExpressionError('CONCAT supera 10.000 caracteres por resultado.')
        return result
    raise ExpressionError('Operación no admitida.')


class CalculationPlan:
    """Resolve dependencies once; evaluate row fields before grouping."""
    def __init__(self, definitions, columns):
        if len(definitions) > 20:
            raise ExpressionError('Se admiten hasta 20 campos calculados por gráfico.')
        self.terms, self.modes, self.definitions = {}, {}, {}
        names = {str(column).strip().casefold() for column in columns}
        for definition in definitions:
            name = definition['name']
            if not name.strip() or name.strip().casefold() in names:
                raise ExpressionError(f'El nombre [{name}] está vacío o duplicado.')
            names.add(name.strip().casefold())
            self.definitions[name] = definition
        visiting, expanded, costs = set(), [0], {}

        def bounded_cost(node):
            identity = id(node)
            if identity not in costs:
                total = 1
                for child in node.children:
                    total += bounded_cost(child)
                    if total > 1024:
                        raise ExpressionError('La expansión de las referencias supera 1.024 operaciones.')
                costs[identity] = total
            return costs[identity]

        def resolve(node, inside_aggregate=False):
            expanded[0] += 1
            if expanded[0] > 1024:
                raise ExpressionError('Las dependencias superan el límite de complejidad.')
            if node.operation == 'field' and node.value in self.definitions:
                term = field(node.value)
                if inside_aggregate and self.modes[node.value] == 'aggregate':
                    raise ExpressionError('No se puede agregar un campo que ya es agregado.')
                return term
            if node.operation == 'field' and node.value not in columns:
                raise ExpressionError(f'No existe el campo [{node.value}].')
            if node.operation == 'call' and node.value == 'SELECTED_COUNT':
                return node
            aggregate = node.operation == 'call' and node.value in AGGREGATES
            if aggregate and inside_aggregate:
                raise ExpressionError('No se admiten agregaciones anidadas.')
            return Term(node.operation, node.value, tuple(resolve(child, inside_aggregate or aggregate) for child in node.children))

        def cardinality(node, inside=False):
            if node.operation == 'call' and node.value == 'SELECTED_COUNT':
                return set()
            aggregate = node.operation == 'call' and node.value in AGGREGATES
            if node.operation == 'field':
                return set() if inside else {'row'}
            result = {'aggregate'} if aggregate else set()
            for child in node.children:
                result |= cardinality(child, inside or aggregate)
            return result

        def field(name):
            if name in self.terms:
                return self.terms[name]
            if name in visiting:
                raise ExpressionError(f'Referencia circular en [{name}].')
            visiting.add(name)
            definition = self.definitions[name]
            node = resolve(compile_expression(definition['expression']))
            bounded_cost(node)
            kinds = cardinality(node)
            mode = definition.get('mode', 'aggregate' if 'aggregate' in kinds else 'row')
            if kinds == {'aggregate', 'row'}:
                raise ExpressionError(f'[{name}] mezcla filas y agregados. Agregá las referencias por fila explícitamente.')
            if mode == 'row' and 'aggregate' in kinds:
                raise ExpressionError(f'[{name}] contiene agregaciones. Elegí evaluación agregada.')
            self.terms[name], self.modes[name] = node, mode
            visiting.remove(name)
            return node

        for name in self.definitions:
            field(name)
        def checked_resolve(node):
            result = resolve(node)
            bounded_cost(result)
            return result
        self.resolve = checked_resolve

    def rows(self, frame, selected=None):
        result = frame.copy()
        for name, term in self.terms.items():
            if self.modes[name] == 'row':
                result[name] = evaluate(term, frame, selected)
        return result

    def value(self, name, frame, selected=None):
        return evaluate(self.terms[name], frame, selected)

    def condition(self, expression, frame, selected=None):
        result = evaluate(self.resolve(compile_expression(expression)), frame, selected)
        if isinstance(result, pd.Series):
            values = result.dropna().unique()
            if len(values) > 1:
                raise ExpressionError('La condición debe producir un único resultado en los datos filtrados.')
            result = values[0] if len(values) else False
        return boolean(result)
