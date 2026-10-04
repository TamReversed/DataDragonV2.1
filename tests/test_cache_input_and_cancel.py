"""T3.1 server pieces: cache_id replaces an upload on any route, owner-scoped; jobs can be cancelled."""
import json
from queue import Queue

import pytest

import datadragon
from helpers import make_csv, output_path, post_form, post_job


def make_result(client, tmp_path):
    path = make_csv(tmp_path / "src.csv", "a,b\n1,x\n2,y\n3,z\n")
    resp, payload = post_form(client, "/row-filter", {"file": path},
                              {"conditions": json.dumps([{"column": "a", "operator": "is_not_empty"}])})
    assert resp.status_code == 200, payload
    files = client.get("/get-cached-files").get_json()["files"]
    return files[0]["cache_id"]


def test_cache_id_stands_in_for_the_upload(client, tmp_path):
    cache_id = make_result(client, tmp_path)
    resp, payload = post_form(client, "/find-replace", None,
                              {"cache_id": cache_id, "find_text": "x", "replace_text": "Q", "column": "b"})
    assert resp.status_code == 200, payload
    assert payload["replacements_made"] == 1


def test_get_columns_accepts_cache_id(client, tmp_path):
    cache_id = make_result(client, tmp_path)
    resp, payload = post_form(client, "/get-columns", None, {"cache_id": cache_id})
    assert resp.status_code == 200 and [c["name"] for c in payload["columns"]] == ["a", "b"]


def test_field_specific_cache_id_for_two_file_tools(client, tmp_path):
    cache_id = make_result(client, tmp_path)
    right = make_csv(tmp_path / "r.csv", "a,w\n1,p\n2,q\n")
    final, _ = post_job(client, "/merge-data", {"right_file": right},
                        {"cache_id.left_file": cache_id, "left_key": "a", "right_key": "a", "join_type": "inner",
                         "duplicate_handling": "keep_all"})
    assert final["stage"] == "done", final
    import openpyxl
    merged = openpyxl.load_workbook(output_path(final))["Merged Data"]
    assert merged.max_row == 3                                  # header + the two matching rows


def test_an_uploaded_file_wins_over_a_cache_id(client, tmp_path):
    cache_id = make_result(client, tmp_path)
    path = make_csv(tmp_path / "other.csv", "k\nfoo\n")
    resp, payload = post_form(client, "/get-columns", {"file": path}, {"cache_id": cache_id})
    assert [c["name"] for c in payload["columns"]] == ["k"]


def test_cache_id_of_another_browser_is_refused(client, tmp_path):
    cache_id = make_result(client, tmp_path)
    other = datadragon.app.test_client()                      # a different browser: its own session cookie
    resp, payload = post_form(other, "/get-columns", None, {"cache_id": cache_id})
    assert resp.status_code == 400 and "No file" in payload["error"]


def test_unknown_cache_id_is_refused(client):
    resp, payload = post_form(client, "/get-columns", None, {"cache_id": "doesnotexist"})
    assert resp.status_code == 400


# ------------------------------------------------------------------ cancel
def test_cancelling_an_unknown_job_is_a_404(client):
    assert client.post("/jobs/nope/cancel").status_code == 404


def test_another_browser_cannot_cancel_my_job():
    q = Queue()
    datadragon.register_job("job_cancel_owner", q, owner="owner-a")
    other = datadragon.app.test_client()
    assert other.post("/jobs/job_cancel_owner/cancel").status_code == 404
    assert "job_cancel_owner" not in datadragon.cancelled_jobs


def test_cancel_stops_a_job_at_its_next_progress_report(client):
    started = Queue()
    q = Queue()
    owner = "owner-cancel"
    datadragon.register_job("job_cancel_1", q, owner=owner)
    q.put({"stage": "working"})                               # fine before the cancel
    datadragon.cancelled_jobs.add("job_cancel_1")
    with pytest.raises(datadragon.JobCancelled):
        q.put({"stage": "working"})
    q.put({"stage": "error", "message": "Cancelled"})         # the terminal message still goes through
    datadragon.cancelled_jobs.discard("job_cancel_1")


def test_cancel_endpoint_ends_a_real_job_with_an_error_message(client, tmp_path, monkeypatch):
    """A job that keeps reporting progress is stopped; the SSE stream ends with an error message."""
    import threading
    import time
    release = threading.Event()
    real = datadragon.compare_files_async

    def slow(*args, **kwargs):
        queue, job = args[-2], args[-1]
        try:
            for i in range(200):
                queue.put({"stage": "working", "percentage": i})
                if i == 2:
                    release.set()
                time.sleep(0.02)
            queue.put({"stage": "done"})
        except Exception as exc:
            queue.put({"stage": "error", "message": str(exc)})

    monkeypatch.setattr(datadragon, "compare_files_async", slow)
    a = make_csv(tmp_path / "a.csv", "id,v\n1,a\n")
    b = make_csv(tmp_path / "b.csv", "id,v\n1,b\n")
    datadragon.rate_limit_store.clear()
    with open(a, "rb") as fa, open(b, "rb") as fb:
        resp = client.post("/compare-data", data={"file1": (fa, "a.csv"), "file2": (fb, "b.csv"),
                                                   "key_columns[]": "id", "compare_columns[]": "v"},
                           content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    job = resp.get_json()["session_id"]
    assert release.wait(5)
    assert client.post(f"/jobs/{job}/cancel").get_json() == {"success": True}
    from helpers import drain_progress
    last = drain_progress(client, job)[-1]
    assert last["stage"] == "error" and "Cancelled" in last["message"]
