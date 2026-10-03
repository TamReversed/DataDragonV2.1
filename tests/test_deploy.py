"""Process model: health check, PORT/HOST, the Procfile, and a progress stream that survives a client disconnect."""
import itertools
import os
import runpy
import time
from queue import Queue

import flask
import pytest

import datadragon

_stream_ids = itertools.count()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}


def test_procfile_runs_one_threaded_worker_on_the_assigned_port():
    line = open(os.path.join(ROOT, "Procfile")).read().strip()
    assert line.startswith("web: gunicorn ") and line.endswith("datadragon:app")
    for part in ("-k gthread", "-w 1", "--threads 8", "-t 0", "-b 0.0.0.0:${PORT:-5002}"):
        assert part in line


@pytest.mark.parametrize("env,expected", [
    ({}, ("127.0.0.1", 5002)),
    ({"PORT": "5099", "HOST": "0.0.0.0"}, ("0.0.0.0", 5099)),
])
def test_main_reads_host_and_port(monkeypatch, env, expected):
    seen = {}
    monkeypatch.setattr(flask.Flask, "run", lambda self, **kwargs: seen.update(kwargs))
    for name in ("PORT", "HOST", "FLASK_DEBUG"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    runpy.run_path(datadragon.__file__, run_name="__main__")
    assert (seen["host"], seen["port"]) == expected and seen["debug"] is False and seen["threaded"] is True


# ------------------------------------------------------------------ progress stream
def stream_job(client, messages):
    """Register a job owned by this browser, with `messages` already queued; return its id and queue."""
    client.get("/")
    with client.session_transaction() as sess:
        owner = sess["owner"]
    sid = f"stream_test_{next(_stream_ids)}"
    q = Queue()
    for message in messages:
        q.put(message)
    datadragon.register_job(sid, q, owner=owner)
    return sid, q


def test_a_client_disconnect_is_not_swallowed_and_the_queue_survives(client):
    sid, q = stream_job(client, [{"stage": "loading", "percentage": 10, "message": "a"}])
    response = client.get(f"/progress/{sid}", buffered=False)
    first = next(iter(response.response))
    assert b'"stage": "loading"' in first
    started = time.time()
    response.response.close()                       # the browser went away
    assert time.time() - started < 2                # the old bare 'except:' kept polling for 5 s per loop
    assert sid in datadragon.progress_queues        # kept, so the EventSource's automatic reconnect can resume


def test_the_stream_resumes_after_a_reconnect(client):
    sid, q = stream_job(client, [{"stage": "loading", "percentage": 10, "message": "a"}])
    response = client.get(f"/progress/{sid}", buffered=False)
    next(iter(response.response))
    response.response.close()
    q.put({"stage": "done", "percentage": 100, "message": "Complete!"})
    body = client.get(f"/progress/{sid}").get_data(as_text=True)
    assert '"stage": "done"' in body
    assert sid not in datadragon.progress_queues    # cleaned up once the final message was delivered


def test_a_complete_stream_ends_after_the_final_message(client):
    sid, q = stream_job(client, [{"stage": "loading", "percentage": 5, "message": "a"},
                                 {"stage": "error", "message": "boom"}])
    body = client.get(f"/progress/{sid}").get_data(as_text=True)
    assert body.count("data: ") == 2 and '"message": "boom"' in body
    assert sid not in datadragon.progress_queues


def test_unknown_or_foreign_sessions_answer_immediately(client):
    started = time.time()
    body = client.get("/progress/does_not_exist").get_data(as_text=True)
    assert "Session not found" in body and time.time() - started < 0.5
