"""T2.13 / D-01: file inputs stay in the tab order (visually hidden, never display:none), have a name, and the
controls that were click-only <div>s are real buttons."""
import re

import pytest

PAGES = ["/", "/find-replace", "/column-operations", "/row-filter", "/transpose", "/data-validation",
         "/duplicate-finder", "/data-merge", "/data-comparison", "/column-comparison", "/pivot-generator",
         "/calculated-columns", "/column-analyzer", "/column-normalizer", "/data-scrubber", "/pdf-to-word",
         "/unique-identifier-finder", "/data-readiness-pipeline"]




@pytest.mark.parametrize("path", PAGES)
def test_file_inputs_are_focusable_and_named(client, path):
    resp = client.get(path)
    if resp.status_code == 404:
        pytest.skip(f"{path} is not a route")
    html = resp.get_data(as_text=True)
    inputs = re.findall(r'<input type="file"[^>]*>', html)
    for tag in inputs:
        assert "visually-hidden" in tag, tag
        assert "aria-label" in tag, tag
        assert "display: none" not in tag and "display:none" not in tag, tag
    assert not re.search(r'input\[type="file"\]\s*\{\s*display:\s*none', html)


def test_main_css_does_not_hide_file_inputs_with_display_none():
    css = open("static/css/main.css", encoding="utf-8").read()
    assert not re.search(r'\.file-upload-input\s*\{\s*display:\s*none', css)
    assert ".visually-hidden" in css


def test_click_only_divs_are_buttons():
    import glob
    for f in glob.glob("templates/*.html"):
        html = open(f, encoding="utf-8").read()
        assert '<div class="cached-file-item"' not in html, f
        assert '<div class="transform-toggle"' not in html, f
        assert '<div class="toggle-switch"' not in html, f
        assert '<div class="examples-toggle"' not in html, f
