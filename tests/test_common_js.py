"""T3.1: pages migrated to static/js/common.js use the shared helpers instead of their own copies."""
import glob
import re

import pytest

# Grows with each migration batch; the last batch makes it every template that has these helpers.
MIGRATED = ["find_replace", "row_filter", "column_operations", "transpose",
            "data_validation", "duplicate_finder", "unique_identifier_finder", "data_scrubber",
            "column_normalizer", "calculated_columns", "column_analyzer", "pivot_generator"]


def read(path):
    return open(path, encoding="utf-8").read()


def test_common_js_is_loaded_by_the_base_layout_before_page_scripts():
    base = read("templates/base.html")
    assert base.index("js/common.js") < base.index("{% block scripts %}")


@pytest.mark.parametrize("name", MIGRATED)
def test_migrated_pages_have_no_private_copies(name):
    html = read(f"templates/{name}.html")
    for private in ("function escapeHtml", "function formatFileSize", "function loadCachedFiles",
                    "function useCachedFile", "/download-cached-file/", "new EventSource"):
        assert private not in html, (name, private)
    assert "DD.initUpload" in html and "DD.cachedFiles" in html
    assert not re.search(r"formData\.append\('file'", html), "uploads go through DD.appendFile"


def test_common_js_defines_the_shared_pieces():
    js = read("static/js/common.js")
    for needed in ("function escapeHtml", "function formatFileSize", "initUpload", "cachedFiles", "startJob",
                   "cancel()", "modal", "MAX_RECONNECTS = 5"):
        assert needed in js
    assert "alert(" not in js
