"""The Calculated Columns formula engine must evaluate known syntax and reject everything else."""
import pandas as pd
import pytest

from datadragon_formula import FormulaError, evaluate_formula


@pytest.fixture
def df():
    return pd.DataFrame({
        "Name": ["  Alice ", "Bob", None],
        "Qty": [1, 2, 3],
        "Price": [1.25, 2.5, None],
        "When": ["2024-03-05", "2023-12-31", "not a date"],
        'We"ird': [10, 20, 30],
    })


def values(series):
    return [None if pd.isna(v) else v for v in series.tolist()]


@pytest.mark.parametrize("formula", [
    "pd.io.common.os.getcwd()",
    "pd.io.common.os.environ.get('HOME')",
    '__df__.__class__',
    "().__class__.__bases__",
    "(lambda: 1)()",
    "[x for x in 'a']",
    "__import__('os').system('true')",
    "open('/etc/passwd')",
    "UPPER.__globals__",
    "[Qty].__class__",
    "[Qty].apply(print)",
    "np.sum([Qty])",
    "UPPER(x=[Name])",
    "UPPER(*[Name])",
    "[Qty] if True else 0",
    "{1: 2}",
    "'a' 'b' ;import os",
    "1; 2",
    "",
    "   ",
])
def test_rejected(df, formula):
    with pytest.raises(ValueError):
        evaluate_formula(df, formula)


def test_rejection_is_formula_error_not_runtime_error(df):
    with pytest.raises(FormulaError):
        evaluate_formula(df, "pd.io.common.os.getcwd()")


@pytest.mark.parametrize("formula,expected", [
    ("[Qty] * 2 + 1", [3, 5, 7]),
    ("[Qty] - 1", [0, 1, 2]),
    ("[Qty] / 2", [0.5, 1.0, 1.5]),
    ("[Qty] % 2", [1, 0, 1]),
    ("[Qty] ** 2", [1, 4, 9]),
    ("-[Qty]", [-1, -2, -3]),
    ("[Qty] > 1", [False, True, True]),
    ("[Qty] >= 2", [False, True, True]),
    ("[Qty] == 2", [False, True, False]),
    ("[Qty] != 2", [True, False, True]),
    ("1 < [Qty] < 3", [False, True, False]),
    ("([Qty] > 1) & ([Qty] < 3)", [False, True, False]),
    ("([Qty] < 2) | ([Qty] > 2)", [True, False, True]),
    ("[Qty] > 1 and [Qty] < 3", [False, True, False]),
    ("[We\"ird] + 1", [11, 21, 31]),
    ("CONCAT([Qty], \"-\", \"x\")", ["1-x", "2-x", "3-x"]),
    ("UPPER([Name])", ["  ALICE ", "BOB", ""]),
    ("LOWER(\"ABC\")", ["abc", "abc", "abc"]),
    ("TRIM([Name])", ["Alice", "Bob", ""]),
    ("LEFT([Name], 3)", ["  A", "Bob", ""]),
    ("LEFT([Name], 0)", ["", "", ""]),
    ("RIGHT([Name], 2)", ["e ", "ob", ""]),
    ("RIGHT([Name], 0)", ["", "", ""]),
    ("RIGHT([Name], 99)", ["  Alice ", "Bob", ""]),
    ("LEN([Name])", [8, 3, 0]),
    ("REPLACE([Name], \"o\", \"0\")", ["  Alice ", "B0b", ""]),
    ("CONCAT([Name], \"!\")", ["  Alice !", "Bob!", "!"]),
    ("ROUND([Price], 1)", [1.2, 2.5, None]),
    ("ABS(-[Qty])", [1, 2, 3]),
    ("CEILING([Price])", [2.0, 3.0, None]),
    ("FLOOR([Price])", [1.0, 2.0, None]),
    ("YEAR([When])", [2024, 2023, None]),
    ("MONTH([When])", [3, 12, None]),
    ("DAY([When])", [5, 31, None]),
    ("ISNULL([Price])", [False, False, True]),
    ("COALESCE([Price], 0)", [1.25, 2.5, 0.0]),
    ("IF([Qty] > 1, \"hi\", \"lo\")", ["lo", "hi", "hi"]),
    ("'it''s'", None),  # two adjacent literals are not valid syntax
])
def test_accepted(df, formula, expected):
    if expected is None:
        with pytest.raises(ValueError):
            evaluate_formula(df, formula)
        return
    got = values(evaluate_formula(df, formula))
    assert got == pytest.approx(expected) if any(isinstance(e, float) for e in expected if e is not None) else got == expected


def test_today_returns_iso_date_per_row(df):
    got = evaluate_formula(df, "TODAY()")
    assert len(got) == 3 and len(set(got)) == 1 and len(got.iloc[0]) == 10


def test_string_literals_containing_brackets_and_quotes_are_text(df):
    assert values(evaluate_formula(df, 'CONCAT("[Qty]", "it\'s")')) == ["[Qty]it's"] * 3


def test_unknown_column_message(df):
    with pytest.raises(ValueError, match='Column "Nope" not found'):
        evaluate_formula(df, "[Nope] + 1")


def test_resource_limits(df):
    for formula in ["[Qty] ** 11", "2 ** 11", '"ab" * 5000', "[Name] * 5000", "1+" * 500 + "1", "(" * 300 + "1" + ")" * 300,
                    "1 + " * 450 + "1"]:
        with pytest.raises(ValueError):
            evaluate_formula(df, formula)
    with pytest.raises(ValueError):
        evaluate_formula(df, "1" + " + 1" * 1000)  # longer than the character limit


def test_no_module_or_df_names_leak(df):
    for name in ("pd", "np", "__df__", "os"):
        with pytest.raises(ValueError):
            evaluate_formula(df, name)


# ---- nested resource attacks found in review: every step is size-checked, not just the top operands
@pytest.mark.parametrize("formula", [
    "((((((10**10)**10)**10)**10)**10)**10)**10",
    "'%999999999d' % 1",
    "\"%s\" % [Name]",
    "(('a'*1000)*1000)*1000",
    "([Name]*1000)*1000",
    "CONCAT(('a'*1000)*30, ('b'*1000)*30)",
    "(2**10)**10**10",
    "((2**10)**10)**10 * 1000000",
])
def test_nested_resource_attacks_are_rejected_quickly(df, formula):
    import time
    started = time.time()
    with pytest.raises(ValueError):
        evaluate_formula(df, formula)
    assert time.time() - started < 2


def test_reasonable_big_numbers_still_work(df):
    assert evaluate_formula(df, "2 ** 10 * 1000").tolist() == [1024000] * 3
    assert evaluate_formula(df, '"ab" * 10').tolist() == ["ab" * 10] * 3
    assert len(evaluate_formula(df, '"x" * 1000 + "y" * 1000').iloc[0]) == 2000
