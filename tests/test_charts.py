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


def test_palette_is_okabe_ito_and_exports_exist():
    js = read("static/js/charts.js")
    for color in ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442"):
        assert color in js
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
    for name, limit in (("datadragon-logo-128.png", 30_000), ("datadragon-logo-256.png", 30_000)):
        assert os.path.getsize(f"static/images/{name}") < limit, name
    assert os.path.exists("design/source/datadragon-logo.png")
    assert not os.path.exists("static/images/datadragon-logo.png")
    for page in ("index", "landing", "security_info"):
        html = read(f"templates/{page}.html")
        assert "datadragon-logo-128.png" in html and "datadragon-logo-256.png" in html and "srcset=" in html
