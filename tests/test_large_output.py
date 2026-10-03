"""T2.11 / A-15: outputs that do not fit an Excel sheet fall back to CSV (single table) or a zip of CSVs (several
tables), with a warning. The Excel limit constant is patched down so the tests stay fast."""
import csv
import io
import json
import os
import zipfile

import pytest

import datadragon
from helpers import make_csv, output_path, post_form, post_job

ROWS = 30
TEXT = "id,v\n" + "\n".join(f"{i},=x{i}" for i in range(ROWS)) + "\n"


@pytest.fixture
def small_excel(monkeypatch):
    monkeypatch.setattr(datadragon, "EXCEL_MAX_ROWS", 20)


def test_single_table_falls_back_to_csv_with_warning(client, tmp_path, small_excel):
    path = make_csv(tmp_path / "big.csv", TEXT)
    resp, payload = post_form(client, "/row-filter", {"file": path},
                              {"conditions": json.dumps([{"column": "id", "operator": "is_not_empty"}])})
    assert resp.status_code == 200, payload
    assert payload["filename"].endswith(".csv")
    assert "CSV" in payload["warning"] and "19" in payload["warning"]
    rows = list(csv.reader(open(output_path(payload), encoding="utf-8")))
    assert len(rows) == ROWS + 1
    assert rows[1][1] == "'=x0"                      # still formula-safe


def test_a_table_that_fits_stays_xlsx(client, tmp_path):
    path = make_csv(tmp_path / "ok.csv", TEXT)
    resp, payload = post_form(client, "/row-filter", {"file": path},
                              {"conditions": json.dumps([{"column": "id", "operator": "is_not_empty"}])})
    assert payload["filename"].endswith(".xlsx") and "warning" not in payload


def test_merge_with_oversize_sheet_becomes_zip_of_csvs(client, tmp_path, small_excel):
    left = make_csv(tmp_path / "l.csv", TEXT)
    right = make_csv(tmp_path / "r.csv", "id,w\n" + "\n".join(f"{i},w{i}" for i in range(ROWS)) + "\n")
    final, _ = post_job(client, "/merge-data", {"left_file": left, "right_file": right},
                        {"left_key": "id", "right_key": "id", "join_type": "inner", "duplicate_handling": "keep_all"})
    assert final["stage"] == "done", final
    assert final["output_filename"].endswith(".zip") and "zip of CSV" in final["warning"]
    z = zipfile.ZipFile(output_path(final))
    assert sorted(z.namelist()) == ["Join Summary.csv", "Merged Data.csv"]
    assert len(list(csv.reader(io.StringIO(z.read("Merged Data.csv").decode())))) == ROWS + 1


def test_pivot_source_sheet_is_skipped_for_big_files(client, tmp_path, monkeypatch):
    import openpyxl
    monkeypatch.setattr(datadragon, "PIVOT_SOURCE_SHEET_LIMIT", 10)
    path = make_csv(tmp_path / "p.csv", "g,n\n" + "\n".join(f"{i % 3},{i}" for i in range(ROWS)) + "\n")
    final, _ = post_job(client, "/generate-pivot", {"file": path},
                        {"rows[]": ["g"], "values[]": ["n"], "aggfunc": "sum"})
    assert final["stage"] == "done", final
    assert "Source Data" not in openpyxl.load_workbook(output_path(final)).sheetnames
    assert "Source Data sheet was left out" in final["warning"]


def test_unique_id_data_goes_into_the_zip_as_csv(client, tmp_path, small_excel):
    path = make_csv(tmp_path / "u.csv", TEXT)
    final, _ = post_job(client, "/find-unique-identifier", {"file": path}, {"selected_columns[]": ["id"]})
    assert final["stage"] == "done", final
    names = zipfile.ZipFile(output_path(final)).namelist()
    assert any(n.endswith("_data.csv") for n in names) and any(n.endswith("_key_candidates.csv") for n in names)
    assert "CSV" in final["warning"]


def test_splitter_chunk_size_is_below_the_excel_limit():
    assert 1000000 < datadragon.EXCEL_MAX_ROWS - 1   # the route caps chunk_size at 1,000,000
