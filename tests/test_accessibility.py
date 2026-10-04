"""T3.3: contrast tokens, focus ring, reduced motion, landmarks and live regions."""
import glob
import importlib.util
import re

import pytest


def read(path):
    return open(path, encoding="utf-8").read()


@pytest.fixture(scope="module")
def contrast():
    spec = importlib.util.spec_from_file_location("contrast", "scripts/contrast.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_text_and_button_pair_meets_wcag_aa(contrast):
    rows = contrast.checks()
    assert len(rows) >= 8
    for label, value, minimum in rows:
        assert value >= minimum, f"{label}: {value:.2f} < {minimum}"


def test_the_old_muted_text_would_have_failed(contrast):
    page = contrast.parse_color("#0a0a0f")
    assert contrast.ratio((255, 255, 255, 0.4), page) < 4.5          # the previous --text-muted
    assert contrast.ratio((255, 255, 255, 1.0), contrast.parse_color("#a855f7")) < 4.5   # the previous button start


def test_focus_ring_and_reduced_motion_rules_exist_and_every_page_loads_them():
    css = read("static/css/a11y.css")
    assert ":focus-visible" in css and "2px solid var(--dd-focus" in css and "outline-offset: 2px" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "css/a11y.css" in read("templates/base.html")              # every page, the hub included, extends base.html
    for page in glob.glob("templates/*.html"):
        if not page.endswith(("base.html", "_tools.html")):
            assert '{% extends "base.html" %}' in read(page), page
    main = read("static/css/main.css")
    assert "@media (prefers-reduced-motion: no-preference)" in main     # the waiting cells animate only for these users
    assert re.search(r"no-preference\)\s*\{\s*\.lava-bubble\s*\{\s*animation:", main)


def test_pages_have_landmarks_a_skip_link_and_hide_decorative_layers():
    html = read("templates/base.html")
    assert "<main" in html and "</main>" in html and 'id="main"' in html
    assert 'class="skip-link" href="#main"' in html
    assert '<nav class="tool-tree" aria-label="Tools">' in html and 'aria-current="page"' in html
    assert 'class="page-wash" aria-hidden="true"' in html
    for name in ("empty_upload", "empty_results", "error", "done", "scales"):     # illustrations are decoration
        assert 'aria-hidden="true"' in read(f"templates/brand/{name}.svg"), name


def test_common_js_adds_live_regions_progressbars_alerts_and_keyboard_scrolling():
    js = read("static/js/common.js")
    for needed in ("role', 'alert'", "role', 'status'", "aria-live", "role', 'progressbar'", "aria-valuenow",
                   "tabindex"):
        assert needed in js


def test_the_pipeline_page_has_a_heading():
    assert '<h1 class="pipeline-title">' in read("templates/data_readiness_pipeline.html")


def test_one_primary_action_once_a_result_is_showing():
    css = read("static/css/main.css")
    assert ".main-content:has(.results.show :is(.btn-primary, .download-btn)) .btn-primary:not(.results *) {" in css
