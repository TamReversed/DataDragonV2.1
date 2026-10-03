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
    """Resolve a message's download_url (/download/<job_id>/<name>) to the file on disk."""
    job_id, name = final["download_url"].rsplit("/", 2)[-2:]
    return os.path.join(datadragon.app.config["OUTPUT_FOLDER"], job_id, name)

def post_form(client, url, files=None, data=None):
    """POST to a synchronous route. Returns (response, json_payload)."""
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
    return resp, resp.get_json(silent=True)


def sync_output(payload):
    """Resolve a synchronous route's download_url to the file under its job directory."""
    return output_path(payload)

def drain_progress(client, session_id):
    return sse_messages(client.get(f"/progress/{session_id}").get_data(as_text=True))


def make_xlsx(path, header, rows, text_cols=()):
    """Write an xlsx with exact control over cell types (text_cols are forced to text cells)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for r in rows:
        ws.append(list(r))
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if header[c.column - 1] in text_cols and c.value is not None:
                c.value = str(c.value)
                c.data_type = "s"
            elif isinstance(c.value, str) and c.value.startswith("="):
                c.data_type = "s"
    wb.save(path)
    return path


def make_csv(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return path
