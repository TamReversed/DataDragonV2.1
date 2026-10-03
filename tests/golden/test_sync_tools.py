"""Characterization tests for tools that answer synchronously with JSON + a file under OUTPUT_FOLDER."""
import json

import pytest

from helpers import fixture_path, post_form, sync_output
from snapshot import describe_file, scrub

GOLDEN_XLSX = fixture_path("golden.xlsx")


def run(client, golden, name, url, data, files=None):
    resp, payload = post_form(client, url, files or {"file": GOLDEN_XLSX}, data)
    snap = {"status": resp.status_code, "json": scrub(payload)}
    if payload and payload.get("filename"):
        snap["output"] = describe_file(sync_output(payload))
    golden(name, snap)


ROW_FILTERS = {
    "equals": [{"column": "Region", "operator": "equals", "value": "N"}],
    "numeric_and": [{"column": "Amount", "operator": "greater_than", "value": "0"},
                    {"column": "Region", "operator": "equals", "value": "S", "logic": "AND"}],
    "or_empty": [{"column": "Region", "operator": "is_empty"},
                 {"column": "Name", "operator": "equals", "value": "Bob", "logic": "OR"}],
    "in_list": [{"column": "Name", "operator": "in_list", "value": "alice, carol"}],
}


@pytest.mark.parametrize("case", sorted(ROW_FILTERS))
def test_row_filter(client, golden, case):
    run(client, golden, f"row_filter_{case}", "/row-filter", {"conditions": json.dumps(ROW_FILTERS[case])})


def test_row_filter_preview_only(client, golden):
    run(client, golden, "row_filter_preview", "/row-filter",
        {"conditions": json.dumps(ROW_FILTERS["equals"]), "preview_only": "true"})


FIND_REPLACE = {
    "plain_all": {"find_text": "alice", "replace_text": "ALICE"},
    "case_sensitive_col": {"find_text": "Bob", "replace_text": "Robert", "column": "Name", "case_sensitive": "true"},
    "regex_col": {"find_text": r"\d+", "replace_text": "#", "column": "Email", "use_regex": "true"},
    "whole_cell": {"find_text": "N", "replace_text": "North", "column": "Region", "match_whole_cell": "true",
                   "case_sensitive": "true"},
}


@pytest.mark.parametrize("case", sorted(FIND_REPLACE))
def test_find_replace(client, golden, case):
    run(client, golden, f"find_replace_{case}", "/find-replace", FIND_REPLACE[case])


CALC = {
    "math": {"formula": "[Account] * 2 + 1", "new_column_name": "Calc"},
    "text": {"formula": 'CONCAT([Name], "-", [Region])', "new_column_name": "Calc"},
    "if": {"formula": 'IF([Account] > 1020, "hi", "lo")', "new_column_name": "Calc"},
    "preview": {"formula": "[Account] * 2", "new_column_name": "Calc", "preview_only": "true"},
}


@pytest.mark.parametrize("case", sorted(CALC))
def test_calculated_columns(client, golden, case):
    run(client, golden, f"calc_{case}", "/calculated-columns", CALC[case])


COLUMN_OPS = {
    "reorder": {"operation": "reorder", "column_order": json.dumps(["Name", "ID", "Zip"])},
    "rename": {"operation": "rename", "renames": json.dumps({"Name": "FullName"})},
    "delete": {"operation": "delete", "columns_to_delete": json.dumps(["Notes", "Pct"])},
    "duplicate": {"operation": "duplicate", "source_column": "Zip", "new_column_name": "Zip2"},
    "split": {"operation": "split", "column_to_split": "Email", "delimiter": "@", "new_names": "user,domain"},
    "merge": {"operation": "merge", "columns_to_merge": json.dumps(["Name", "Region"]), "separator": "-",
              "new_column_name": "NameRegion"},
}


@pytest.mark.parametrize("case", sorted(COLUMN_OPS))
def test_column_operations(client, golden, case):
    run(client, golden, f"column_ops_{case}", "/column-operations", COLUMN_OPS[case])
