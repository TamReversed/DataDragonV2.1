"""Characterization tests: lock in the CURRENT output of each background-job tool.

Snapshots record today's behaviour, including known-wrong behaviour (see tests/test_known_bugs.py).
"""
import json

import pytest

from helpers import fixture_path, output_path, post_job
from snapshot import describe_file, scrub

GOLDEN_XLSX = fixture_path("golden.xlsx")
GOLDEN_CSV = fixture_path("golden.csv")


def run(client, golden, name, url, files, data=None):
    final, msgs = post_job(client, url, files, data)
    assert final["stage"] == "done", final
    snap = {"final": scrub(final), "stages": sorted({m.get("stage") for m in msgs})}
    if final.get("download_url"):
        snap["output"] = describe_file(output_path(final))
    golden(name, snap)


def test_split_xlsx(client, golden):
    run(client, golden, "split_xlsx", "/upload", {"file": GOLDEN_XLSX},
        {"chunk_size": "15", "base_filename": "gold"})


@pytest.mark.parametrize("src", ["xlsx", "csv"])
def test_analyze_dataframe(dd, golden, src):
    path = GOLDEN_XLSX if src == "xlsx" else GOLDEN_CSV
    # 'Flag' (boolean) is excluded: analysis currently crashes on bool columns (A-21,
    # covered by tests/test_known_bugs.py::test_analyze_bool_column).
    # Read like the analyzer tool does (analyze_file_async uses mode='infer'): the analysis needs real types.
    df = dd.read_data_file(path, mode="infer").drop(columns=["Flag"])
    analysis = dd.analyze_dataframe(df)
    golden(f"analyze_{src}", scrub(dd.make_json_serializable(analysis)))


def test_duplicates(client, golden):
    run(client, golden, "duplicates", "/find-duplicates", {"file": GOLDEN_XLSX},
        {"id_column": "ID", "duplicate_columns[]": ["Zip", "Region"]})


def test_unique_identifier(client, golden):
    run(client, golden, "unique_identifier", "/find-unique-identifier", {"file": GOLDEN_XLSX},
        {"selected_columns[]": ["ID", "Zip", "Account", "Region"]})


@pytest.mark.parametrize("join,dup", [("inner", "keep_all"), ("left", "keep_first"), ("outer", "keep_all")])
def test_merge(client, golden, join, dup):
    run(client, golden, f"merge_{join}_{dup}", "/merge-data",
        {"left_file": fixture_path("merge_left.csv"), "right_file": fixture_path("merge_right.csv")},
        {"left_key": "id", "right_key": "id", "join_type": join, "duplicate_handling": dup})


def test_compare(client, golden):
    run(client, golden, "compare", "/compare-data",
        {"file1": fixture_path("compare_a.csv"), "file2": fixture_path("compare_b.csv")},
        {"key_columns[]": ["k1"]})


@pytest.mark.parametrize("agg", ["sum", "mean"])
def test_pivot(client, golden, agg):
    run(client, golden, f"pivot_{agg}", "/generate-pivot", {"file": GOLDEN_XLSX},
        {"rows[]": ["Region"], "columns[]": ["Flag"], "values[]": ["Amount"], "aggfunc": agg})


def test_validate(client, golden):
    rules = [
        {"column": "Amount", "type": "required"},
        {"column": "Account", "type": "range", "value": {"min": 1001, "max": 1030}},
        {"column": "Zip", "type": "pattern", "value": r"^\d{5}$"},
        {"column": "Region", "type": "list", "value": ["N", "S"]},
    ]
    run(client, golden, "validate", "/validate-data", {"file": GOLDEN_XLSX},
        {"validation_rules": json.dumps(rules)})


def test_normalize(client, golden):
    types = {"Amount": "float", "EuroNum": "float", "Pct": "percentage", "DateText": "date", "Account": "integer"}
    run(client, golden, "normalize", "/normalize-columns", {"file": GOLDEN_XLSX},
        {"column_types": json.dumps(types), "trim_whitespace": "false"})


@pytest.mark.parametrize("relationship", ["false", "true"])
def test_scrub(client, golden, relationship):
    run(client, golden, f"scrub_relationship_{relationship}", "/scrub-data", {"file": GOLDEN_XLSX},
        {"columns[]": ["Name", "Email"], "relationship_preserve": relationship, "export_mapping": "true"})


def test_compare_columns(client, golden):
    run(client, golden, "compare_columns", "/compare-columns",
        {"file1": GOLDEN_CSV, "file2": GOLDEN_XLSX})


def test_transpose(client, golden):
    run(client, golden, "transpose", "/transpose-data", {"file": fixture_path("compare_a.csv")})
