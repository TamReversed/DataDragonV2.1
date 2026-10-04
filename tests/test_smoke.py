import io

import openpyxl
import pytest

PAGES = [
    "/", "/landing", "/excel-splitter", "/column-analyzer", "/security-info", "/data-scrubber",
    "/duplicate-finder", "/unique-identifier-finder", "/data-merge", "/data-comparison",
    "/pivot-generator", "/data-validation", "/column-normalizer", "/pdf-to-word",
    "/column-comparison", "/transpose", "/row-filter", "/find-replace", "/calculated-columns",
    "/column-operations", "/data-readiness-pipeline",
]


@pytest.mark.parametrize("path", PAGES)
def test_page_loads(client, path):
    assert client.get(path).status_code == 200


def test_generate_test_file_row_count(client):
    resp = client.get("/generate-test-file?num_rows=50")
    assert resp.status_code == 200
    ws = openpyxl.load_workbook(io.BytesIO(resp.data)).active
    assert ws.max_row - 1 == 50
