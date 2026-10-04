"""T4.2: tool search on the landing page, route aliases, consistent preview tables."""
import glob
import re

import pytest


@pytest.mark.parametrize("path", ["/data-scrubber", "/data-anonymizer", "/unique-identifier-finder", "/natural-key-finder"])
def test_old_and_new_route_names_both_work(client, path):
    assert client.get(path).status_code == 200


def test_aliases_serve_the_same_pages(client):
    assert client.get("/data-anonymizer").get_data() == client.get("/data-scrubber").get_data()
    assert client.get("/natural-key-finder").get_data() == client.get("/unique-identifier-finder").get_data()


def test_landing_has_a_labelled_search_box_with_a_slash_shortcut(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="toolSearch"' in html and 'for="toolSearch"' in html and 'type="search"' in html
    assert "event.key === '/'" in html and 'aria-live="polite"' in html and "[hidden] { display: none !important; }" in html


def test_preview_tables_share_height_and_overscroll_rules():
    for path in glob.glob("templates/*.html"):
        for block in re.findall(r"\.preview-table-wrapper \{[^}]*\}", open(path, encoding="utf-8").read()):
            assert "max-height: 360px" in block and "overscroll-behavior: auto" in block, path


def test_main_css_has_the_shared_small_screen_rules():
    css = open("static/css/main.css", encoding="utf-8").read()
    assert "@media (max-width: 640px)" in css and ".btn-group {" in css and ".preview-table-wrapper," in css


def test_landing_links_use_the_new_names_and_the_shortcut_respects_open_dialogs(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/natural-key-finder"' in html and 'href="/data-anonymizer"' in html
    assert 'href="/data-scrubber"' not in html and 'href="/unique-identifier-finder"' not in html
    assert "testFileModal').style.display === 'flex'" in html and "select, button, [role=dialog]" in html
