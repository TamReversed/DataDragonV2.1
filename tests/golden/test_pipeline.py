"""Characterization test for the Data Readiness Pipeline (start -> analyze -> keys -> confirm -> execute)."""
import os

import datadragon
from helpers import drain_progress, fixture_path, output_path
from snapshot import describe_file, scrub


def start(client, path):
    datadragon.rate_limit_store.clear()
    with open(path, "rb") as fh:
        resp = client.post("/pipeline/start", data={"file": (fh, os.path.basename(path))},
                           content_type="multipart/form-data")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()


def final_of(msgs):
    return next(m for m in msgs if m.get("stage") in ("done", "error", "complete"))


def test_pipeline_full_flow(client, golden, tmp_path):
    # 'Flag' (boolean) is dropped: analysis crashes on bool columns (A-21), which would leave stage 1
    # empty. That crash and the execute-without-analysis crash are covered in tests/test_known_bugs.py.
    src = tmp_path / "golden_noflag.csv"
    with open(fixture_path("golden.csv"), encoding="utf-8") as fh:
        text = fh.read().splitlines()
    import csv
    rows = list(csv.reader(text))
    flag = rows[0].index("Flag")
    with open(src, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows([r[:flag] + r[flag + 1:] for r in rows])
    snap = {}
    started = start(client, str(src))
    sid = started["session_id"]
    snap["start"] = scrub({k: v for k, v in started.items() if k != "preview"})

    assert client.post(f"/pipeline/{sid}/analyze").status_code == 200
    msgs = drain_progress(client, sid)
    snap["analyze_stages"] = sorted({m.get("stage") for m in msgs})
    state = client.get(f"/pipeline/{sid}/state").get_json()
    snap["state_after_analyze"] = scrub(state)

    gaps = client.get(f"/pipeline/{sid}/gaps")
    snap["gaps_status"] = gaps.status_code
    snap["gaps"] = scrub(gaps.get_json())

    resp = client.post(f"/pipeline/{sid}/keys", json={"selected_columns": ["ID", "Zip", "Account", "Region"]})
    assert resp.status_code == 200
    msgs = drain_progress(client, sid)
    snap["keys_stages"] = sorted({m.get("stage") for m in msgs})
    snap["keys_final"] = scrub(final_of(msgs))

    resp = client.post(f"/pipeline/{sid}/keys/confirm", json={"selected_key": ["ID"]})
    snap["confirm"] = scrub(resp.get_json())

    trans = client.get(f"/pipeline/{sid}/transformations")
    snap["transformations_status"] = trans.status_code
    snap["transformations"] = scrub(trans.get_json())

    selections = {"anonymization": {"enabled": True, "columns": ["Name"]},
                  "normalization": {"enabled": True, "columns": ["EuroNum"]}}
    resp = client.post(f"/pipeline/{sid}/transformations/select", json=selections)
    snap["select"] = scrub(resp.get_json())

    resp = client.post(f"/pipeline/{sid}/execute")
    assert resp.status_code == 200
    msgs = drain_progress(client, sid)
    final = final_of(msgs)
    assert final["stage"] != "error", final
    snap["execute_final"] = scrub(final)
    for key in ("download_url", "zip_url", "result_url"):
        if final.get(key):
            snap["execute_output"] = describe_file(output_path({"download_url": final[key]}))
            break
    golden("pipeline_full_flow", snap)
