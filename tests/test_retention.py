"""Retention and robustness: expiry by TTL, re-downloadable results, a bounded job pool, a sweeper that survives errors."""
import itertools
import os
import threading
import time
from queue import Queue

import pytest

import datadragon
from helpers import fixture_path, post_form, post_job

GOLDEN_XLSX = fixture_path("golden.xlsx")
HOUR = 3600
_ids = itertools.count()


def output_root():
    return datadragon.app.config["OUTPUT_FOLDER"]


def upload_root():
    return datadragon.app.config["UPLOAD_FOLDER"]


def make_job_dir(name, age_seconds, files=("result.xlsx",)):
    path = os.path.join(output_root(), name)
    os.makedirs(path, exist_ok=True)
    stamp = time.time() - age_seconds
    for f in files:
        file_path = os.path.join(path, f)
        with open(file_path, "wb") as fh:
            fh.write(b"x")
        os.utime(file_path, (stamp, stamp))
    os.utime(path, (stamp, stamp))
    return path


# ------------------------------------------------------------------ outputs
def test_old_job_folders_are_removed_whether_or_not_they_were_downloaded():
    old = make_job_dir("retention_old", datadragon.OUTPUT_TTL_SECONDS + 60)
    fresh = make_job_dir("retention_fresh", 60)
    datadragon.cleanup_outputs()
    assert not os.path.exists(old) and os.path.exists(fresh)


def test_a_folder_with_a_recent_file_is_kept_even_if_the_folder_is_old():
    path = make_job_dir("retention_mixed", datadragon.OUTPUT_TTL_SECONDS + 600, files=("old.xlsx",))
    with open(os.path.join(path, "new.xlsx"), "wb") as fh:
        fh.write(b"y")
    datadragon.cleanup_outputs()
    assert os.path.exists(path)


def test_the_ttl_is_configurable_by_time_not_only_by_default(monkeypatch):
    monkeypatch.setattr(datadragon, "OUTPUT_TTL_SECONDS", 10)
    path = make_job_dir("retention_short", 30)
    datadragon.cleanup_outputs()
    assert not os.path.exists(path)


def test_cleanup_accepts_an_explicit_clock():
    path = make_job_dir("retention_clock", 0)
    datadragon.cleanup_outputs(now=time.time() + datadragon.OUTPUT_TTL_SECONDS + 10)
    assert not os.path.exists(path)


def test_a_download_can_be_repeated_within_the_ttl(client):
    final, _ = post_job(client, "/find-duplicates", {"file": GOLDEN_XLSX}, {"id_column": "ID", "duplicate_columns[]": ["Zip"]})
    first, second = client.get(final["download_url"]), client.get(final["download_url"])
    assert first.status_code == second.status_code == 200 and first.data == second.data


def test_downloads_stop_working_once_the_sweeper_removed_the_folder(client):
    final, _ = post_job(client, "/find-duplicates", {"file": GOLDEN_XLSX}, {"id_column": "ID", "duplicate_columns[]": ["Zip"]})
    assert client.get(final["download_url"]).status_code == 200
    datadragon.cleanup_outputs(now=time.time() + datadragon.OUTPUT_TTL_SECONDS + 10)
    assert client.get(final["download_url"]).status_code == 404


def test_cached_file_entries_expire_with_their_files(client):
    resp, payload = post_form(client, "/row-filter", {"file": GOLDEN_XLSX},
                              {"conditions": '[{"column": "Name", "operator": "equals", "value": "Bob"}]'})
    assert resp.status_code == 200 and len(datadragon.file_cache) == 1
    for entry in datadragon.file_cache.values():
        entry["timestamp"] -= datadragon.OUTPUT_TTL_SECONDS + 10
    datadragon.cleanup_session_cache()
    assert datadragon.file_cache == {}


# ------------------------------------------------------------------ uploads
def test_stray_uploads_are_swept_and_fresh_ones_kept():
    old, fresh = os.path.join(upload_root(), "stray_old.xlsx"), os.path.join(upload_root(), "stray_fresh.xlsx")
    for path in (old, fresh):
        open(path, "wb").write(b"x")
    stamp = time.time() - datadragon.UPLOAD_TTL_SECONDS - 60
    os.utime(old, (stamp, stamp))
    datadragon.cleanup_uploads()
    assert not os.path.exists(old) and os.path.exists(fresh)
    os.remove(fresh)


@pytest.mark.parametrize("url,data", [
    ("/row-filter", {"conditions": '[{"column": "Name", "operator": "equals", "value": "Bob"}]'}),
    ("/find-replace", {"find_text": "Bob", "replace_text": "Rob", "column": "Name"}),
    ("/calculated-columns", {"formula": "[Account] * 2", "new_column_name": "d"}),
    ("/column-operations", {"operation": "rename", "renames": '{"Name": "N"}'}),
])
def test_sync_tools_do_not_keep_the_upload(client, url, data):
    before = set(os.listdir(upload_root()))
    resp, payload = post_form(client, url, {"file": GOLDEN_XLSX}, data)
    assert resp.status_code == 200, payload
    assert set(os.listdir(upload_root())) == before


def test_a_failed_sync_request_does_not_keep_the_upload_either(client):
    before = set(os.listdir(upload_root()))
    resp, _ = post_form(client, "/find-replace", {"file": GOLDEN_XLSX}, {"find_text": "(unclosed", "use_regex": "true"})
    assert resp.status_code == 400
    assert set(os.listdir(upload_root())) == before


# ------------------------------------------------------------------ progress queues
def test_uncollected_progress_queues_expire():
    sid = f"queue_test_{next(_ids)}"
    datadragon.register_job(sid, Queue(), owner="o")
    datadragon.cleanup_progress_queues()
    assert sid in datadragon.progress_queues                              # still young
    datadragon.cleanup_progress_queues(now=time.time() + datadragon.QUEUE_TTL_SECONDS + 5)
    assert sid not in datadragon.progress_queues and sid not in datadragon.progress_queue_created


# ------------------------------------------------------------------ pipeline sessions
def add_session(owner, age_idle=0, sid=None):
    sid = sid or f"pipe_{next(_ids)}"
    state = datadragon.PipelineState(sid, None, "f.csv", owner=owner)
    state.df = object()
    state.last_activity = time.time() - age_idle
    datadragon.pipeline_sessions[sid] = state
    return sid


@pytest.fixture(autouse=True)
def _clean_pipeline_sessions():
    datadragon.pipeline_sessions.clear()
    yield
    datadragon.pipeline_sessions.clear()


def test_idle_pipeline_sessions_expire_on_last_activity_not_creation():
    old_but_active = add_session("a")
    datadragon.pipeline_sessions[old_but_active].created_at -= 5 * HOUR           # created long ago ...
    idle = add_session("a", age_idle=datadragon.PIPELINE_IDLE_SECONDS + 60)       # ... versus untouched for hours
    datadragon.cleanup_pipeline_sessions()
    assert old_but_active in datadragon.pipeline_sessions and idle not in datadragon.pipeline_sessions


def test_using_a_session_counts_as_activity(client):
    client.get("/")
    with client.session_transaction() as sess:
        owner = sess["owner"]
    sid = add_session(owner, age_idle=HOUR)
    before = datadragon.pipeline_sessions[sid].last_activity
    assert client.get(f"/pipeline/{sid}/state").status_code == 200
    assert datadragon.pipeline_sessions[sid].last_activity > before + HOUR - 5


def test_one_browser_keeps_at_most_three_sessions_least_recently_used_first():
    ids = [add_session("a", age_idle=100 - i * 10) for i in range(3)]           # ids[0] is the least recent
    datadragon.make_room_for_pipeline("a")
    assert ids[0] not in datadragon.pipeline_sessions and set(ids[1:]) <= set(datadragon.pipeline_sessions)
    assert datadragon.pipeline_sessions.__len__() == datadragon.MAX_PIPELINES_PER_OWNER - 1


def test_the_total_number_of_sessions_is_capped_across_browsers():
    for i in range(datadragon.MAX_PIPELINES_TOTAL):
        add_session(f"owner{i}", age_idle=1000 - i)
    datadragon.make_room_for_pipeline("newcomer")
    assert len(datadragon.pipeline_sessions) == datadragon.MAX_PIPELINES_TOTAL - 1


def test_an_evicted_session_frees_its_dataframe_and_file(tmp_path):
    path = tmp_path / "u.csv"
    path.write_text("a\n1\n")
    sid = add_session("a")
    datadragon.pipeline_sessions[sid].file_path = str(path)
    state = datadragon.pipeline_sessions[sid]
    datadragon.evict_pipeline_session(sid)
    assert state.df is None and not path.exists() and sid not in datadragon.pipeline_sessions
    datadragon.evict_pipeline_session(sid)           # evicting twice is harmless


def test_starting_a_fourth_session_through_the_route_evicts_the_oldest(client):
    ids = []
    for _ in range(4):
        datadragon.rate_limit_store.clear()
        with open(fixture_path("compare_a.csv"), "rb") as fh:
            ids.append(client.post("/pipeline/start", data={"file": (fh, "a.csv")},
                                   content_type="multipart/form-data").get_json()["session_id"])
        time.sleep(0.01)
    live = [sid for sid in ids if sid in datadragon.pipeline_sessions]
    assert len(live) == datadragon.MAX_PIPELINES_PER_OWNER and ids[0] not in live


# ------------------------------------------------------------------ sweeper robustness and the job pool
def test_a_failing_cleanup_step_does_not_stop_the_others(monkeypatch):
    calls = []
    monkeypatch.setattr(datadragon, "cleanup_outputs", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(datadragon, "cleanup_uploads", lambda: calls.append("uploads"))
    monkeypatch.setattr(datadragon, "cleanup_progress_queues", lambda: calls.append("queues"))
    datadragon.run_cleanup()                          # must not raise
    assert calls == ["uploads", "queues"]


def test_sweeping_tolerates_dicts_that_change_while_it_runs():
    datadragon.file_cache.clear()
    for i in range(50):
        datadragon.file_cache[f"c{i}"] = {"timestamp": 0, "path": "", "owner": "o"}
    stop = threading.Event()

    def mutate():
        n = 0
        while not stop.is_set():
            datadragon.file_cache[f"extra{n % 20}"] = {"timestamp": time.time(), "path": "", "owner": "o"}
            datadragon.file_cache.pop(f"extra{(n + 7) % 20}", None)
            n += 1

    worker = threading.Thread(target=mutate)
    worker.start()
    try:
        for _ in range(200):
            datadragon.cleanup_session_cache()        # used to raise "dictionary changed size during iteration"
    finally:
        stop.set()
        worker.join()
    assert not any(k.startswith("c") and k[1:].isdigit() for k in datadragon.file_cache)


def test_the_cleanup_thread_is_the_only_raw_thread():
    source = open(datadragon.__file__).read()
    assert source.count("threading.Thread(") == 1
    assert "executor.submit" in source


def test_jobs_run_in_a_bounded_pool():
    gate, started, active, peak = threading.Event(), [], [0], [0]
    lock = threading.Lock()

    def job():
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        started.append(1)
        gate.wait(5)
        with lock:
            active[0] -= 1

    futures = [datadragon.start_job(job) for _ in range(datadragon.MAX_JOBS + 3)]
    time.sleep(0.3)
    assert peak[0] <= datadragon.MAX_JOBS and len(started) <= datadragon.MAX_JOBS      # the rest are queued
    gate.set()
    for f in futures:
        f.result(timeout=10)
    assert len(started) == datadragon.MAX_JOBS + 3


def test_a_crashing_job_is_logged_not_fatal(caplog):
    datadragon.log.addHandler(caplog.handler)          # the app logger does not propagate to the root logger
    try:
        future = datadragon.start_job(lambda: 1 / 0)
        with pytest.raises(ZeroDivisionError):
            future.result(timeout=5)
        time.sleep(0.1)
        assert "background job crashed" in caplog.text
    finally:
        datadragon.log.removeHandler(caplog.handler)
    assert datadragon.start_job(lambda: 42).result(timeout=5) == 42                    # the pool still works


def test_env_settings_are_read(monkeypatch):
    monkeypatch.setenv("DATADRAGON_TEST_MINUTES", "2.5")
    assert datadragon._minutes("DATADRAGON_TEST_MINUTES", 30) == 150
    monkeypatch.setenv("DATADRAGON_TEST_MINUTES", "junk")
    assert datadragon._minutes("DATADRAGON_TEST_MINUTES", 30) == 30 * 60
    assert datadragon._minutes("DATADRAGON_UNSET_VALUE", 7) == 7 * 60
