import json
import os

import datadragon

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture_path(name):
    return os.path.join(FIXTURES, name)


def sse_messages(body):
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def post_job(client, url, files=None, data=None):
    """POST to a job-starting route, follow its SSE progress stream, return (final_message, all_messages).

    files: {field: path}. The rate limiter is reset first so loops of calls are not throttled.
    """
    datadragon.rate_limit_store.clear()
    form = dict(data or {})
    handles = []
    try:
        for field, path in (files or {}).items():
            fh = open(path, "rb")
            handles.append(fh)
            form[field] = (fh, os.path.basename(path))
        resp = client.post(url, data=form, content_type="multipart/form-data")
    finally:
        for fh in handles:
            fh.close()
    payload = resp.get_json(silent=True) or {}
    sid = payload.get("session_id")
    if not sid:
        return {"stage": "http_error", "status": resp.status_code, "body": payload}, []
    msgs = sse_messages(client.get(f"/progress/{sid}").get_data(as_text=True))
    final = next((m for m in msgs if m.get("stage") in ("done", "error")), msgs[-1] if msgs else None)
    return final, msgs


def output_path(final):
    """Resolve a final message's download_url to a file under OUTPUT_FOLDER."""
    url = final["download_url"]
    return os.path.join(datadragon.app.config["OUTPUT_FOLDER"], url.rsplit("/", 1)[-1])
