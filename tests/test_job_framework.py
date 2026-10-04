"""T3.2: shared job runner, generic client errors, structured logging, JSON serializer (A-18)."""
import json
import math
import os
from queue import Queue

import numpy as np
import pandas as pd
import pytest

import datadragon
from helpers import make_csv, post_form, post_job


@pytest.fixture
def app_log(caplog):
    datadragon.log.addHandler(caplog.handler)          # the app logger does not propagate to the root logger
    caplog.set_level("DEBUG", logger="datadragon")
    yield caplog
    datadragon.log.removeHandler(caplog.handler)


# ------------------------------------------------------------------ error hygiene
def test_unexpected_route_errors_are_generic_and_logged_with_a_reference(client, tmp_path, monkeypatch, app_log):
    def boom(*args, **kwargs):
        raise RuntimeError("secret-cell-value-4711")
    monkeypatch.setattr(datadragon, "read_data_file", boom)
    path = make_csv(tmp_path / "a.csv", "a\n1\n")
    resp, payload = post_form(client, "/get-columns", {"file": path}, {})
    assert resp.status_code == 500
    assert "secret-cell-value-4711" not in json.dumps(payload)
    ref = payload["error"].split("ref ")[1].rstrip(")")
    assert payload["error"] == f"Processing failed (ref {ref})"
    assert ref in app_log.text and "secret-cell-value-4711" in app_log.text      # the details are for the log only


def test_user_errors_keep_their_message_and_are_400(client, tmp_path):
    path = make_csv(tmp_path / "a.csv", "a\n1\n")
    resp, payload = post_form(client, "/column-operations", {"file": path},
                              {"operation": "rename", "renames": json.dumps({"zzz": "q"})})
    assert resp.status_code == 400 and 'Column "zzz" not found' in payload["error"]


def test_unexpected_job_errors_are_generic_and_the_input_is_removed(client, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret-cell-value-4711")
    monkeypatch.setattr(datadragon, "read_data_file", boom)
    path = make_csv(tmp_path / "a.csv", "a\n1\n")
    uploads = datadragon.app.config["UPLOAD_FOLDER"]
    before = set(os.listdir(uploads))
    final, _ = post_job(client, "/transpose-data", {"file": path})
    assert final["stage"] == "error" and final["message"].startswith("Processing failed (ref ")
    assert "secret" not in final["message"]
    assert set(os.listdir(uploads)) == before             # the worker removed its upload


def test_user_errors_in_jobs_keep_their_message(client, tmp_path):
    path = make_csv(tmp_path / "a.csv", "k,v\n1,a\n")
    final, _ = post_job(client, "/validate-data", {"file": path},
                        {"validation_rules": json.dumps([{"column": "k", "type": "range", "value": {"min": "low"}}])})
    assert final["stage"] == "error" and "not a number" in final["message"]


def test_job_worker_reports_cancellation_and_sets_the_job_id_for_logs(app_log):
    seen = []

    @datadragon.job_worker()
    def worker(progress_queue, session_id):
        seen.append(datadragon.current_job_id.get())
        datadragon.log.info("working")
        raise datadragon.JobCancelled("Cancelled")

    q = Queue()
    worker(q, "job_ctx_1")
    assert seen == ["job_ctx_1"] and q.get_nowait() == {"stage": "error", "message": "Cancelled"}
    assert datadragon.current_job_id.get() == "-"                    # restored afterwards
    assert any(r.job == "job_ctx_1" for r in app_log.records if r.getMessage() == "working"), \
        [(r.getMessage(), getattr(r, "job", None)) for r in app_log.records]


def test_progress_sender_keeps_the_message_shape():
    q = Queue()
    datadragon.progress_sender(q, "j")("loading", 5, 10, "half", None)
    assert q.get_nowait() == {"stage": "loading", "current": 5, "total": 10, "percentage": 50, "message": "half"}
    datadragon.progress_sender(None, "j")("loading", 1, 1, "ignored")       # no queue: nothing happens


def test_the_source_has_no_print_calls_or_leaked_exception_text():
    source = open("datadragon.py", encoding="utf-8").read()
    assert "print(" not in source
    assert "'error': str(e)" not in source


# ------------------------------------------------------------------ A-18: JSON that browsers can read
def test_serializer_turns_nan_and_infinity_into_null_even_inside_arrays():
    data = {"f": float("nan"), "g": np.float64("inf"), "arr": np.array([1.0, np.nan, 3.0]), "n": np.int64(4),
            "b": np.bool_(True), "ts": pd.Timestamp("2024-01-02"), "nat": pd.NaT, "na": pd.NA, "s": "x",
            "nested": [np.nan, {"k": -math.inf}]}
    cleaned = datadragon.make_json_serializable(data)
    assert cleaned == {"f": None, "g": None, "arr": [1.0, None, 3.0], "n": 4, "b": True, "ts": "2024-01-02 00:00:00",
                       "nat": None, "na": None, "s": "x", "nested": [None, {"k": None}]}
    assert "NaN" not in json.dumps(cleaned) and "Infinity" not in json.dumps(cleaned)


def test_df_preview_shapes():
    df = pd.DataFrame({"i": [1, 2], "f": [1.5, np.nan], "s": ["a", None], "b": [True, False],
                       "d": pd.to_datetime(["2024-01-02", None])})
    rows = datadragon.df_preview(df, 1)
    assert rows == [{"i": 1, "f": 1.5, "s": "a", "b": True, "d": "2024-01-02 00:00:00"}]
    assert datadragon.df_preview(df, 5)[1] == {"i": 2, "f": None, "s": None, "b": False, "d": None}
    assert datadragon.df_preview_text(df, 5)[1] == {"i": "2", "f": None, "s": None, "b": "False", "d": None}


def test_pipeline_start_preview_is_valid_json(client, tmp_path):
    from test_pipeline_flow import start
    sid = start(client, make_csv(tmp_path / "n.csv", "a,b\n1,\n,x\n"))
    resp = client.get(f"/pipeline/{sid}/state")
    assert resp.status_code == 200
    json.loads(resp.get_data(as_text=True), parse_constant=lambda c: pytest.fail(f"bare {c} in JSON"))
