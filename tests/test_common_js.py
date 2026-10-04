"""T3.1: pages migrated to static/js/common.js use the shared helpers instead of their own copies."""
import glob
import re

import pytest

# Every tool page, including the Excel Splitter (index.html, rebuilt on the base layout in T3.6).
MIGRATED = ["find_replace", "row_filter", "column_operations", "transpose",
            "data_validation", "duplicate_finder", "unique_identifier_finder", "data_scrubber",
            "column_normalizer", "calculated_columns", "column_analyzer", "pivot_generator",
            "data_merge", "data_comparison", "column_comparison", "data_readiness_pipeline", "pdf_to_word",
            "index"]
NO_CACHED_LIST = {"data_readiness_pipeline", "pdf_to_word", "index"}      # these pages never offered earlier results


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
    assert "DD.initUpload" in html
    assert name in NO_CACHED_LIST or "DD.cachedFiles" in html
    assert not re.search(r"formData\.append\('file'", html), "uploads go through DD.appendFile"


def test_common_js_defines_the_shared_pieces():
    js = read("static/js/common.js")
    for needed in ("function escapeHtml", "function formatFileSize", "initUpload", "cachedFiles", "startJob",
                   "cancel()", "modal", "MAX_RECONNECTS = 5"):
        assert needed in js
    assert "alert(" not in js


def test_no_template_still_defines_its_own_escapehtml():
    leftovers = [p for p in glob.glob("templates/*.html") if "function escapeHtml" in read(p)]
    assert leftovers == []


def test_the_splitter_page_is_on_the_base_layout_without_the_theme_playground():
    html = read("templates/index.html")
    assert html.count('extends "base.html"') == 1
    for gone in ("uiToggle", "color-picker", "speedSlider", "future-mode", "blob", "localStorage"):
        assert gone not in html, gone
    assert 'value="40000"' in html and 'id="baseFilename"' in html and "/upload" in html
