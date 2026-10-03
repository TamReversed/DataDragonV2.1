"""Jobs, cached files, analysis results, pipeline sessions and downloads belong to one browser."""
import json
import os
import time

import pytest

import datadragon
from helpers import fixture_path, post_form, post_job, sse_messages

GOLDEN_XLSX = fixture_path("golden.xlsx")


@pytest.fixture
def other():
    """A second browser: its own cookie jar, therefore its own owner id."""
    return datadragon.app.test_client()


def owner_of(client):
    client.get("/")
    with client.session_transaction() as sess:
        return sess["owner"]


def start_split(client):
    datadragon.rate_limit_store.clear()
    with open(fixture_path("compare_a.csv"), "rb") as fh:
        resp = client.post("/upload", data={"file": (fh, "a.xlsx"), "chunk_size": "10"},
                           content_type="multipart/form-data")
    return resp


def test_each_browser_gets_its_own_stable_owner(client, other):
    first = owner_of(client)
    assert first and owner_of(client) == first
    assert owner_of(other) != first


def test_other_browser_cannot_stream_progress(client, other):
    # Use a real analyze job: the owner sees a normal stream, the other browser sees "Session not found".
    final, msgs = post_job(client, "/find-duplicates", {"file": GOLDEN_XLSX},
                           {"id_column": "ID", "duplicate_columns[]": ["Zip"]})
    assert final["stage"] == "done"

    datadragon.rate_limit_store.clear()
    with open(GOLDEN_XLSX, "rb") as fh:
        sid = client.post("/find-duplicates", data={"file": (fh, "g.xlsx"), "id_column": "ID",
                                                    "duplicate_columns[]": ["Zip"]},
                          content_type="multipart/form-data").get_json()["session_id"]
    stolen = sse_messages(other.get(f"/progress/{sid}").get_data(as_text=True))
    assert stolen == [{"error": "Session not found"}]
    own = sse_messages(client.get(f"/progress/{sid}").get_data(as_text=True))
    assert own[-1]["stage"] == "done"


def test_download_link_belongs_to_the_browser_that_received_it(client, other):
    final, _ = post_job(client, "/find-duplicates", {"file": GOLDEN_XLSX},
                        {"id_column": "ID", "duplicate_columns[]": ["Zip"]})
    url = final["download_url"]
    assert other.get(url).status_code == 404
    resp = client.get(url)
    assert resp.status_code == 200 and len(resp.data) > 100


def test_unclaimed_files_are_not_downloadable_by_anyone(client):
    job_folder = os.path.join(datadragon.app.config["OUTPUT_FOLDER"], "unclaimed_job")
    os.makedirs(job_folder, exist_ok=True)
    with open(os.path.join(job_folder, "unclaimed_file.xlsx"), "wb") as fh:
        fh.write(b"x")
    assert client.get("/download/unclaimed_job/unclaimed_file.xlsx").status_code == 404  # job nobody owns
    assert client.get("/download/unclaimed_file.xlsx").status_code == 404                # old flat URL is gone


def test_fetch_analysis_is_owner_only(client, other):
    owner = owner_of(client)
    sid = "analyze_20260101_000000_0123456789abcdef"
    datadragon.job_registry.bind(sid, owner)
    datadragon.analysis_results[sid] = {"data": {"rows": 1}, "timestamp": time.time()}
    assert other.get(f"/fetch-analysis/{sid}").status_code == 404
    assert datadragon.analysis_results.get(sid) is not None  # the failed attempt must not consume it
    assert client.get(f"/fetch-analysis/{sid}").get_json()["analysis"] == {"rows": 1}


def test_use_cached_file_is_owner_only_and_hides_server_paths(client, other):
    resp, payload = post_form(client, "/row-filter", {"file": GOLDEN_XLSX},
                              {"conditions": json.dumps([{"column": "Name", "operator": "equals", "value": "Bob"}])})
    assert resp.status_code == 200
    cache_id = client.get("/get-cached-files").get_json()["files"][0]["cache_id"]

    datadragon.rate_limit_store.clear()
    mine = client.post("/use-cached-file", json={"cache_id": cache_id})
    assert mine.status_code == 200
    assert "path" not in mine.get_json() and datadragon.app.config["OUTPUT_FOLDER"] not in mine.get_data(as_text=True)

    datadragon.rate_limit_store.clear()
    theirs = other.post("/use-cached-file", json={"cache_id": cache_id})
    assert theirs.status_code == 400
    assert other.get(f"/download-cached-file/{cache_id}").status_code == 404
    assert other.get("/get-cached-files").get_json()["files"] == []


def test_cache_limit_is_per_owner(dd, tmp_path):
    def make(name):
        path = tmp_path / name
        path.write_bytes(b"x")
        return str(path)

    other_id = dd.cache_session_file("s", "theirs.xlsx", make("theirs.xlsx"), 1, 1, "t", owner="B")
    mine = [dd.cache_session_file("s", f"m{i}.xlsx", make(f"m{i}.xlsx"), 1, 1, "t", owner="A") for i in range(12)]
    assert sum(1 for v in dd.file_cache.values() if v["owner"] == "A") == 10
    assert other_id in dd.file_cache                       # B's entry survives A's churn
    assert mine[0] not in dd.file_cache and mine[-1] in dd.file_cache


def test_pipeline_session_is_owner_only(client, other):
    datadragon.rate_limit_store.clear()
    with open(fixture_path("compare_a.csv"), "rb") as fh:
        sid = client.post("/pipeline/start", data={"file": (fh, "a.csv")},
                          content_type="multipart/form-data").get_json()["session_id"]
    assert client.get(f"/pipeline/{sid}/state").status_code == 200
    for method, path in [("get", "state"), ("post", "analyze"), ("get", "gaps"), ("post", "keys"),
                         ("post", "execute"), ("get", "transformations")]:
        resp = getattr(other, method)(f"/pipeline/{sid}/{path}")
        assert resp.status_code == 404, (method, path)
        assert resp.get_json()["error"] == "Pipeline session not found or expired"
    assert sse_messages(other.get(f"/progress/{sid}").get_data(as_text=True)) == [{"error": "Session not found"}]
