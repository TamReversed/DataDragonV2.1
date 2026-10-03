"""find_minimal_keys: agrees with a brute-force search, reports truncation, ignores constant columns."""
import itertools
import random

import pandas as pd

import datadragon
from datadragon import find_minimal_keys


def brute_force(df, cols, max_size=5):
    keys = []
    for k in range(1, min(len(cols), max_size) + 1):
        for combo in itertools.combinations(cols, k):
            if any(set(key) <= set(combo) for key in keys):
                continue
            if not df.duplicated(subset=list(combo)).any():
                keys.append(list(combo))
    return keys


def test_matches_brute_force_on_random_tables():
    rng = random.Random(3)
    for trial in range(40):
        n_cols = rng.randint(2, 6)
        n_rows = rng.randint(1, 25)
        cols = [f"c{i}" for i in range(n_cols)]
        df = pd.DataFrame({c: [rng.randint(0, rng.choice([1, 2, 4, 30])) for _ in range(n_rows)] for c in cols})
        result = find_minimal_keys(df, cols, max_keys=10_000)
        assert not result["truncated"]
        assert {frozenset(k) for k in result["keys"]} == {frozenset(k) for k in brute_force(df, cols)}, (trial, df)


def test_sample_screening_gives_the_same_answer(monkeypatch):
    rng = random.Random(5)
    df = pd.DataFrame({"a": range(300), "b": [rng.randint(0, 3) for _ in range(300)],
                       "c": [rng.randint(0, 3) for _ in range(300)]})
    df.loc[299, "a"] = 0                       # the repeat is outside a 50-row sample
    full = find_minimal_keys(df, ["a", "b", "c"], sample_rows=10_000)["keys"]
    screened = find_minimal_keys(df, ["a", "b", "c"], sample_rows=50)["keys"]
    assert full == screened


def test_nulls_count_as_equal_values():
    df = pd.DataFrame({"a": [1, None, None], "b": [1, 2, 3]})
    assert find_minimal_keys(df, ["a", "b"])["keys"] == [["b"]]


def test_candidate_cap_reports_truncation():
    df = pd.DataFrame({f"c{i}": [j % 3 for j in range(40)] for i in range(8)})
    result = find_minimal_keys(df, list(df.columns), max_candidates=12)
    assert result["truncated"] and "candidate limit" in result["reason"]


def test_time_budget_reports_truncation():
    df = pd.DataFrame({f"c{i}": [j % 3 for j in range(200)] for i in range(14)})
    result = find_minimal_keys(df, list(df.columns), time_budget_s=0)
    assert result["truncated"] and "time budget" in result["reason"]


def test_constant_columns_are_never_part_of_a_key():
    df = pd.DataFrame({"k": [1, 1, 2, 2], "const": ["x"] * 4, "v": [1, 2, 1, 2]})
    assert find_minimal_keys(df, ["k", "const", "v"])["keys"] == [["k", "v"]]
