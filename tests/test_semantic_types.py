"""Semantic type detection (A-11): confidences are clamped and the most specific type wins."""
import pandas as pd
import pytest


def detect(dd, values, name="col"):
    return dd.detect_semantic_type(pd.Series(values), name)


@pytest.mark.parametrize("name,values,expected", [
    ("ID", [f"PR-{i:05d}" for i in range(50)], "ID/Serial"),
    ("Invoice_Date", [f"2024-01-{d:02d}" for d in range(1, 29)] * 2, "Date"),
    ("Zip", ["02134", "90210", "10001", "60601", "30301"] * 4, "Postal Code"),
    ("Region", ["North", "South", "East", "West"] * 10, "Text"),
    ("Email", [f"user{i}@example.com" for i in range(30)], "Email"),
    ("Phone", ["(555) 123-4567", "555-987-6543", "5551234567"] * 5, "Phone Number"),
    ("Site", [f"https://example.com/{i}" for i in range(30)], "URL"),
    ("Price", ["$10.50", "$1,200.00", "$3"] * 5, "Currency"),
    ("Cur", ["USD", "EUR", "GBP"] * 5, "Currency"),
    ("Share", ["10%", "25.5%", "100%"] * 5, "Percentage"),
    ("Flag", ["yes", "no", "yes", "no"] * 5, "Boolean"),
    ("Qty", list(range(100, 160)), "Integer"),
    ("Score", [0.1, 0.25, 0.5, 0.75] * 5, "Float"),
    ("pct", [0.1, 0.25, 0.5, 0.75] * 5, "Percentage"),
    ("BigId", [1234567890123456 + i for i in range(30)], "Integer"),
    ("Card", ["4111 1111 1111 1111", "5500-0000-0000-0004"] * 5, "Credit Card"),
])
def test_detected_type(dd, name, values, expected):
    assert detect(dd, values, name)["detected_type"] == expected


def test_confidence_never_exceeds_100(dd):
    for values in (["5551234567"] * 20, ["02134"] * 20, ["a@b.co"] * 20, ["USD"] * 20, list(range(20))):
        assert 0 <= detect(dd, values)["confidence"] <= 100


def test_repeated_ids_are_not_id_serial(dd):
    assert detect(dd, ["AB123", "CD456"] * 20, "Code")["detected_type"] == "Text"


def test_zero_one_ints_are_boolean_only_when_two_values(dd):
    assert detect(dd, [0, 1, 1, 0] * 5)["detected_type"] == "Boolean"
    assert detect(dd, [0, 0, 0, 0])["detected_type"] == "Integer"
