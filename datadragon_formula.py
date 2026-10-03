"""Safe formula evaluation for the Calculated Columns tool.

Formulas look like  CONCAT([First], " ", [Last])  or  IF([Amount] > 100, "big", "small").
The formula text is parsed with ``ast`` and walked by a small allow-list interpreter. User text is
never turned into executable source: ``[Column]`` references and string literals are replaced by
opaque placeholders before parsing, only a fixed set of node types is accepted, attribute access and
subscripts are rejected, and no modules are reachable from inside a formula.
"""
import ast
import functools
import operator
import re
import secrets
import time
from datetime import datetime

import numpy as np
import pandas as pd

MAX_FORMULA_CHARS = 2000
MAX_NODES = 400
MAX_EXPONENT = 10     # largest exponent accepted when it is a plain number
MAX_REPEAT = 1000     # largest multiplier accepted when repeating text
MAX_TEXT_LENGTH = 32767   # longest text any step may produce (an Excel cell holds 32,767 characters)
MAX_INT_BITS = 256        # largest whole number any step may produce
MAX_SECONDS = 5           # wall-clock budget for one formula


class FormulaError(ValueError):
    """The formula is not valid or uses something that is not allowed."""


def build_function_map(df):
    """Functions available inside formulas (name -> callable)."""

    def to_series(val):
        if isinstance(val, pd.Series):
            return val
        return pd.Series([val] * len(df))

    def to_str_series(val):
        """Text of each value; a blank cell is empty text (never the word 'None'/'nan')."""
        if isinstance(val, pd.Series):
            return val.astype(object).where(val.notna(), '').astype(str)
        return pd.Series(['' if val is None or (isinstance(val, float) and val != val) else str(val)] * len(df))

    def count(n):
        return max(int(n), 0)

    def handle_if(condition, true_val, false_val):
        cond = to_series(condition)
        return pd.Series(np.where(cond, true_val, false_val))

    return {
        # Text functions
        'CONCAT': lambda *args: pd.concat([to_str_series(arg) for arg in args], axis=1).agg(''.join, axis=1),
        'UPPER': lambda x: to_str_series(x).str.upper(),
        'LOWER': lambda x: to_str_series(x).str.lower(),
        'TRIM': lambda x: to_str_series(x).str.strip(),
        'LEFT': lambda x, n: to_str_series(x).str[:count(n)],
        'RIGHT': lambda x, n: to_str_series(x).map(lambda t: t[len(t) - min(count(n), len(t)):]),
        'LEN': lambda x: to_str_series(x).str.len(),
        'REPLACE': lambda x, old, new: to_str_series(x).str.replace(str(old), str(new), regex=False),

        # Math functions
        'ROUND': lambda x, decimals=0: pd.to_numeric(to_series(x), errors='coerce').round(int(decimals)),
        'ABS': lambda x: pd.to_numeric(to_series(x), errors='coerce').abs(),
        'CEILING': lambda x: pd.to_numeric(to_series(x), errors='coerce').apply(lambda v: np.ceil(v) if pd.notna(v) else v),
        'FLOOR': lambda x: pd.to_numeric(to_series(x), errors='coerce').apply(lambda v: np.floor(v) if pd.notna(v) else v),

        # Date functions
        'YEAR': lambda x: pd.to_datetime(to_series(x), errors='coerce').dt.year,
        'MONTH': lambda x: pd.to_datetime(to_series(x), errors='coerce').dt.month,
        'DAY': lambda x: pd.to_datetime(to_series(x), errors='coerce').dt.day,
        'TODAY': lambda: pd.Series([datetime.now().strftime('%Y-%m-%d')] * len(df)),

        # Conditional functions
        'ISNULL': lambda x: to_series(x).isna(),
        'COALESCE': lambda *args: pd.concat([to_series(arg) for arg in args], axis=1).bfill(axis=1).iloc[:, 0],
        'IF': handle_if,
    }


_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_,
}
_CMPOPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
}


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_text_like(v):
    return isinstance(v, str) or (isinstance(v, pd.Series) and v.dtype == object)


def _check_size(value):
    """Reject a result that is absurdly large: nested repeats/powers could otherwise exhaust memory."""
    if isinstance(value, str):
        if len(value) > MAX_TEXT_LENGTH:
            raise FormulaError(f'Result is too long (maximum {MAX_TEXT_LENGTH} characters)')
    elif isinstance(value, int) and not isinstance(value, bool):
        if value.bit_length() > MAX_INT_BITS:
            raise FormulaError('Result is too large')
    elif isinstance(value, pd.Series) and value.dtype == object and len(value):
        longest = value.map(lambda v: len(v) if isinstance(v, str) else 0).max()
        if longest > MAX_TEXT_LENGTH:
            raise FormulaError(f'Result is too long (maximum {MAX_TEXT_LENGTH} characters)')
    return value


class _Interpreter:
    def __init__(self, df, funcs, literals, columns):
        self.df, self.funcs, self.literals, self.columns = df, funcs, literals, columns
        self.nodes = 0
        self.deadline = time.monotonic() + MAX_SECONDS

    def visit(self, node):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FormulaError('Formula is too complex')
        if time.monotonic() > self.deadline:
            raise FormulaError(f'Formula took longer than {MAX_SECONDS} seconds')
        handler = getattr(self, 'v_' + type(node).__name__, None)
        if handler is None:
            raise FormulaError(f'Unsupported syntax in formula ({type(node).__name__})')
        return handler(node)

    def v_Expression(self, node):
        return self.visit(node.body)

    def v_Constant(self, node):
        if node.value is None or isinstance(node.value, (str, int, float, bool)):
            return node.value
        raise FormulaError('Unsupported value in formula')

    def v_Name(self, node):
        if node.id in self.literals:
            try:
                return ast.literal_eval(self.literals[node.id])
            except (ValueError, SyntaxError):
                raise FormulaError('Invalid text value in formula')
        if node.id in self.columns:
            return self.df[self.columns[node.id]]
        raise FormulaError('Unknown name in formula. Reference columns as [ColumnName] and call functions as NAME(...)')

    def v_BinOp(self, node):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise FormulaError('Unsupported operator in formula')
        left, right = self.visit(node.left), self.visit(node.right)
        if isinstance(node.op, ast.Pow) and _is_number(right) and abs(right) > MAX_EXPONENT:
            raise FormulaError(f'Exponent too large (maximum {MAX_EXPONENT})')
        if isinstance(node.op, ast.Mult):
            for count, other in ((left, right), (right, left)):
                if _is_number(count) and abs(count) > MAX_REPEAT and _is_text_like(other):
                    raise FormulaError(f'Cannot repeat text more than {MAX_REPEAT} times')
        if isinstance(node.op, ast.Mod) and _is_text_like(left):
            raise FormulaError('Text formatting with % is not supported')   # "'%999999999d' % 1" builds a huge string
        return _check_size(op(left, right))

    def v_UnaryOp(self, node):
        operand = self.visit(node.operand)
        if isinstance(node.op, ast.USub):
            return operator.neg(operand)
        if isinstance(node.op, ast.UAdd):
            return operator.pos(operand)
        if isinstance(node.op, (ast.Not, ast.Invert)):
            return operator.invert(operand) if isinstance(operand, pd.Series) else (not operand)
        raise FormulaError('Unsupported operator in formula')

    def v_BoolOp(self, node):
        join = operator.and_ if isinstance(node.op, ast.And) else operator.or_
        return functools.reduce(join, [self.visit(v) for v in node.values])

    def v_Compare(self, node):
        left = self.visit(node.left)
        result = None
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _CMPOPS.get(type(op_node))
            if op is None:
                raise FormulaError('Unsupported comparison in formula')
            right = self.visit(comparator)
            piece = op(left, right)
            result = piece if result is None else (result & piece)
            left = right
        return result

    def v_Call(self, node):
        if not isinstance(node.func, ast.Name) or node.func.id not in self.funcs:
            raise FormulaError('Unknown function in formula')
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise FormulaError('Named or unpacked arguments are not supported')
        args = [self.visit(a) for a in node.args]
        try:
            return _check_size(self.funcs[node.func.id](*args))
        except TypeError as e:
            raise FormulaError(f'{node.func.id}(): {e}')


def _preprocess(df, formula):
    """Swap string literals and [Column] references for placeholders so user text never becomes code."""
    token = secrets.token_hex(4)
    literals, columns = {}, {}

    def save_string(match):
        key = f'__s{token}_{len(literals)}__'
        literals[key] = match.group(0)
        return key

    def column_ref(match):
        name = match.group(1)
        if name not in df.columns:
            raise FormulaError(f'Column "{name}" not found')
        key = f'__c{token}_{len(columns)}__'
        columns[key] = name
        return key

    work = re.sub(r'"[^"]*"', save_string, formula)
    work = re.sub(r"'[^']*'", save_string, work)
    work = re.sub(r'\[([^\]]+)\]', column_ref, work)
    return work, literals, columns


def evaluate_formula(df, formula):
    """Evaluate ``formula`` against ``df`` and return a pandas Series (one value per row)."""
    if not isinstance(formula, str) or not formula.strip():
        raise FormulaError('Formula is empty')
    if len(formula) > MAX_FORMULA_CHARS:
        raise FormulaError(f'Formula is too long (maximum {MAX_FORMULA_CHARS} characters)')

    work, literals, columns = _preprocess(df, formula)
    try:
        tree = ast.parse(work.strip(), mode='eval')
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:
        raise FormulaError(f'Invalid formula syntax: {e}')

    interpreter = _Interpreter(df, build_function_map(df), literals, columns)
    try:
        result = interpreter.visit(tree)
    except RecursionError:
        raise FormulaError('Formula is nested too deeply')

    if isinstance(result, pd.DataFrame):
        result = result.iloc[:, 0]
    elif not isinstance(result, pd.Series):
        result = pd.Series([result] * len(df))
    return result
