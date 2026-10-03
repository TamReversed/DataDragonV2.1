"""Every job writes only inside output/<job_id>/ and is downloaded via /download/<job_id>/<name>."""
import json
import os
import re
import zipfile

import datadragon
from helpers import fixture_path, output_path, post_form, post_job

GOLDEN_XLSX = fixture_path("golden.xlsx")
URL = re.compile(r"^/download/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


def output_root():
    return datadragon.app.config["OUTPUT_FOLDER"]


def test_async_tool_output_lives_in_its_job_directory(client):
    final, _ = post_job(client, "/find-duplicates", {"file": GOLDEN_XLSX},
                        {"id_column": "ID", "duplicate_columns[]": ["Zip"]})
    job_id, name = URL.match(final["download_url"]).groups()
    assert os.path.isfile(os.path.join(output_root(), job_id, name))
    assert client.get(final["download_url"]).status_code == 200


def test_nothing_is_written_directly_into_the_output_root(client):
    post_job(client, "/find-duplicates", {"file": GOLDEN_XLSX}, {"id_column": "ID", "duplicate_columns[]": ["Zip"]})
    post_form(client, "/row-filter", {"file": GOLDEN_XLSX},
              {"conditions": json.dumps([{"column": "Name", "operator": "equals", "value": "Bob"}])})
    stray = [n for n in os.listdir(output_root()) if os.path.isfile(os.path.join(output_root(), n))]
    assert stray == []


def test_sync_tools_return_a_job_scoped_download_url(client):
    resp, payload = post_form(client, "/row-filter", {"file": GOLDEN_XLSX},
                              {"conditions": json.dumps([{"column": "Name", "operator": "equals", "value": "Bob"}])})
    assert resp.status_code == 200
    assert URL.match(payload["download_url"])
    assert payload["download_url"].endswith(payload["filename"])
    assert client.get(payload["download_url"]).status_code == 200


def test_back_to_back_sync_jobs_do_not_share_files(client):
    urls, paths = [], []
    for value in ("Bob", "Alice"):
        _, payload = post_form(client, "/row-filter", {"file": GOLDEN_XLSX},
                               {"conditions": json.dumps([{"column": "Name", "operator": "equals", "value": value}])})
        urls.append(payload["download_url"])
        paths.append(output_path(payload))
    assert urls[0] != urls[1] and paths[0] != paths[1]
    assert all(os.path.isfile(p) for p in paths)


def test_splitter_keeps_only_its_zip_in_the_job_directory(client):
    final, _ = post_job(client, "/upload", {"file": GOLDEN_XLSX}, {"chunk_size": "15", "base_filename": "gold"})
    job_id, name = URL.match(final["download_url"]).groups()
    assert os.listdir(os.path.join(output_root(), job_id)) == [name]      # temporary chunk folder is gone
    assert len(zipfile.ZipFile(output_path(final)).namelist()) == 3


def test_anonymizer_mapping_key_is_in_the_same_job_directory(client):
    final, _ = post_job(client, "/scrub-data", {"file": GOLDEN_XLSX},
                        {"columns[]": ["Name"], "relationship_preserve": "false", "export_mapping": "true"})
    assert final["stage"] == "done", final
    job_id, _ = URL.match(final["download_url"]).groups()
    mapping_job, mapping_name = URL.match(final["mapping_url"]).groups()
    assert mapping_job == job_id and mapping_name.startswith("mapping_key_")
    assert client.get(final["mapping_url"]).status_code == 200


def test_download_rejects_traversal_and_bad_segments(client):
    final, _ = post_job(client, "/find-duplicates", {"file": GOLDEN_XLSX},
                        {"id_column": "ID", "duplicate_columns[]": ["Zip"]})
    job_id, name = URL.match(final["download_url"]).groups()
    for path in (f"/download/{job_id}/..%2F{name}", f"/download/..%2F{job_id}/{name}", f"/download/{job_id}/{name}%00",
                 f"/download/{job_id}/..", "/download/../etc/passwd", f"/download/{job_id}/%2e%2e%2f%2e%2e%2fsecret"):
        assert client.get(path).status_code in (400, 404), path
    assert client.get(final["download_url"]).status_code == 200  # the legitimate file is still served
