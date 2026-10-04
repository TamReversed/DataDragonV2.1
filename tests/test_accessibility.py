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
    assert ":focus-visible" in css and "2px solid var(--accent-purple)" in css and "outline-offset: 2px" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    for page in ("base", "landing"):          # index.html (the splitter) now extends base.html
        assert "css/a11y.css" in read(f"templates/{page}.html"), page
    main = read("static/css/main.css")
    assert "@media (prefers-reduced-motion: no-preference)" in main     # orb animation and blur only for these users
    assert re.search(r"no-preference\)\s*\{\s*\.gradient-orb\s*\{\s*animation:", main)


@pytest.mark.parametrize("page", ["base", "landing"])
def test_pages_have_a_main_landmark_and_hide_decorative_layers(page):
    html = read(f"templates/{page}.html")
    assert "<main" in html and "</main>" in html
    assert 'class="gradient-bg" aria-hidden="true"' in html


def test_common_js_adds_live_regions_progressbars_alerts_and_keyboard_scrolling():
    js = read("static/js/common.js")
    for needed in ("role', 'alert'", "role', 'status'", "aria-live", "role', 'progressbar'", "aria-valuenow",
                   "tabindex"):
        assert needed in js


def test_the_pipeline_page_has_a_heading():
    assert '<h1 class="pipeline-title">' in read("templates/data_readiness_pipeline.html")
