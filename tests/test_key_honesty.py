"""T2.8: keys are tested on the full data, blanks are not keys, exports keep every row, and the pipeline only
claims transforms it applied."""
import io
import zipfile

import openpyxl
import pandas as pd

import datadragon
from helpers import make_csv, output_path, pdf_text, post_job
from test_pipeline_flow import run_stage, start


def keys_for(client, tmp_path, text, columns, **extra):
    sid = start(client, make_csv(tmp_path / "k.csv", text))
    run_stage(client, sid, "analyze")
    run_stage(client, sid, "keys", selected_columns=columns, **extra)
    return datadragon.pipeline_sessions[sid].stage_data[3]


def test_exact_duplicates_are_reported_next_to_the_keys(client, tmp_path):
    res = keys_for(client, tmp_path, "id,v\n1,a\n1,a\n2,b\n", ["id", "v"])
    assert res["minimal_combinations"] == []
    assert res["duplicate_rows"] == 1
    assert res["rows_analyzed"] == 3
    assert ["id"] in res["minimal_combinations_after_dedup"]


def test_clean_data_has_no_after_dedup_list(client, tmp_path):
    res = keys_for(client, tmp_path, "id,v\n1,a\n2,a\n", ["id", "v"])
    assert res["minimal_combinations"] == [["id"]]
    assert res["minimal_combinations_after_dedup"] == [] and res["duplicate_rows"] == 0


def test_columns_with_blanks_are_not_keys_unless_allowed(client, tmp_path):
    text = "id,v\n1,a\n2,\n3,c\n"
    res = keys_for(client, tmp_path, text, ["id", "v"])
    assert res["minimal_combinations"] == [["id"]] and res["excluded_null_columns"] == ["v"]
    res = keys_for(client, tmp_path, text, ["id", "v"], allow_null_keys=True)
    assert ["v"] in res["minimal_combinations"] and res["excluded_null_columns"] == []


def test_unique_id_export_keeps_all_rows_and_flags_repeats(client, tmp_path):
    path = make_csv(tmp_path / "u.csv", "id,v\n1,a\n1,a\n2,b\n3,\n")
    final, _ = post_job(client, "/find-unique-identifier", {"file": path}, {"selected_columns[]": ["id", "v"]})
    assert final["stage"] == "done", final
    z = zipfile.ZipFile(output_path(final))
    data = [n for n in z.namelist() if n.endswith("_data.xlsx")][0]
    ws = openpyxl.load_workbook(io.BytesIO(z.read(data)))["Data with Unique IDs"]
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    assert len(rows) == 5                                    # header + all 4 rows
    flags = [r[header.index("_is_duplicate")] for r in rows[1:]]
    assert flags == [False, True, False, False]
    assert final["minimal_columns"] == ["id"]


def test_pipeline_log_says_not_applied_for_normalization(client, tmp_path):
    sid = start(client, make_csv(tmp_path / "n.csv", "id,d\n1,2024-01-01\n2,2024-01-02\n"))
    run_stage(client, sid, "analyze")
    client.post(f"/pipeline/{sid}/transformations/select", json={
        "normalization": {"enabled": True, "columns": ["d"]},
        "gap_handling": {"enabled": True, "columns": ["d"]},
    })
    msgs = run_stage(client, sid, "execute")
    log = msgs[-1]["transformation_log"]
    assert {e["type"]: e["status"] for e in log} == {"normalization": "recorded, not applied",
                                                      "gap_handling": "recorded, not applied"}
    zip_path = output_path(msgs[-1])
    z = zipfile.ZipFile(zip_path)
    pdf = pdf_text(z.read([n for n in z.namelist() if n.endswith(".pdf")][0]))
    assert "not applied" in pdf


def test_anonymization_is_logged_as_applied(client, tmp_path):
    sid = start(client, make_csv(tmp_path / "a.csv", "name\nAnn\nBob\nAnn\n"))
    run_stage(client, sid, "analyze")
    client.post(f"/pipeline/{sid}/transformations/select", json={"anonymization": {"enabled": True, "columns": ["name"]}})
    msgs = run_stage(client, sid, "execute")
    assert msgs[-1]["transformation_log"][0]["status"] == "applied"


def test_unimplemented_methods_are_not_offered():
    html = open("templates/data_readiness_pipeline.html", encoding="utf-8").read()
    for word in ("realistic fake", "SHA-256", "Partial masking", "[REDACTED]", "Fill with default"):
        assert word not in html
