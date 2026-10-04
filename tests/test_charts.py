"""T3.4: shared chart system, per-column canvas ids, SRI on every CDN script."""
import glob
import re


def read(path):
    return open(path, encoding="utf-8").read()


def test_every_cdn_script_tag_has_an_integrity_hash_and_crossorigin():
    tags = []
    for path in glob.glob("templates/*.html"):
        tags += [(path, t) for t in re.findall(r"<script\b[^>]*\bsrc=\"https?://[^>]*>", read(path))]
    assert len(tags) >= 3
    for path, tag in tags:
        assert re.search(r'integrity="sha(256|384|512)-', tag) and 'crossorigin="anonymous"' in tag, (path, tag)


def test_dynamically_loaded_scripts_also_carry_integrity():
    html = read("templates/column_comparison.html")
    assert "script.integrity = 'sha256-" in html and "script.crossOrigin = 'anonymous'" in html


def test_chart_js_is_pinned_to_the_unminified_name_that_exists_in_the_package():
    # the jsDelivr package ships chart.umd.js (already minified); chart.umd.min.js is generated on the fly and has no stable hash
    for page in ("column_analyzer", "data_readiness_pipeline"):
        assert "chart.js@4.4.0/dist/chart.umd.js\"" in read(f"templates/{page}.html")
        assert "chart.umd.min.js" not in read(f"templates/{page}.html")


def test_html2canvas_is_not_loaded_where_it_is_unused():
    assert "html2canvas" not in read("templates/column_analyzer.html")


def test_palette_comes_from_the_tokens_and_exports_exist():
    js = read("static/js/charts.js")
    assert "'--dd-viz-' + (i + 1)" in js and "dd:themechange" in js      # charts follow the theme
    css = read("static/css/tokens.css")
    for i in range(1, 9):
        assert css.count(f"--dd-viz-{i}:") == 3, i                       # light, dark (system), dark (forced)
    for color in ("#56B4E9", "#CC79A7", "#F0E442", "#009E73", "#E69F00"):  # Okabe-Ito hues in the dark theme
        assert color in css
    for needed in ("horizontalBars", "doughnut", "addDownloadButton", "TOP_N = 25", "title: items => shown[items[0].dataIndex].label"):
        assert needed in js


def test_per_column_canvases_are_numbered_not_named():
    html = read("templates/column_analyzer.html")
    assert "numericChart_${Number(idx)}" in html and "categoricalChart_${Number(idx)}" in html
    assert "replace(/[^a-zA-Z0-9]/g" not in html           # two columns named "a b" and "a_b" used to share a canvas id


def test_analyzer_pdf_embeds_chart_images_compressed():
    html = read("templates/column_analyzer.html")
    assert html.count("pdf.addImage(") >= 2 and "compress: true" in html and "'FAST'" in html


def test_pipeline_charts_use_the_shared_helpers():
    html = read("templates/data_readiness_pipeline.html")
    assert "DDCharts.doughnut" in html and "DDCharts.horizontalBars" in html
    assert "new Chart(" not in html


def test_logo_is_served_in_right_sized_files_with_srcset():
    import os
    for name, limit in (("datadragon-mark.svg", 20_000), ("datadragon-mark-dark.svg", 20_000), ("favicon.svg", 20_000),
                        ("favicon-32.png", 5_000), ("apple-touch-icon.png", 20_000)):
        assert os.path.getsize(f"static/images/brand/{name}") < limit, name
    assert os.path.exists("design/source/brand/mark-a.svg")              # the master stays out of static/
    assert not os.path.exists("static/images/datadragon-logo.png")
    base = read("templates/base.html")                                   # the mark is inline SVG, so it follows the theme
    assert '{% include "brand/mark_paths.svg" %}' in base and "brand/favicon.svg" in base
    assert "currentColor" in read("templates/brand/mark_paths.svg")
