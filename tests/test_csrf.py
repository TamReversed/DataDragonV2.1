"""Cross-origin state-changing requests are rejected; same-origin and header-less requests proceed."""
import pytest

import datadragon

FORM = {"formula": "1", "new_column_name": "x", "preview_only": "true"}  # no file -> 400 if the request gets through


@pytest.mark.parametrize("url", ["/calculated-columns", "/upload", "/pipeline/start", "/find-replace",
                                 "/merge-data", "/scrub-data"])
def test_cross_origin_post_blocked(client, url):
    resp = client.post(url, data=FORM, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert "Cross-origin" in resp.get_json()["error"]


def test_null_origin_blocked(client):
    assert client.post("/calculated-columns", data=FORM, headers={"Origin": "null"}).status_code == 403


def test_cross_site_referer_without_origin_blocked(client):
    resp = client.post("/calculated-columns", data=FORM, headers={"Referer": "https://evil.example/page"})
    assert resp.status_code == 403


def test_origin_with_different_port_blocked(client):
    resp = client.post("/calculated-columns", data=FORM, headers={"Origin": "http://localhost:9999"})
    assert resp.status_code == 403


@pytest.mark.parametrize("headers", [
    {},                                                      # curl / scripts / tests
    {"Origin": "http://localhost"},                          # same origin (test client host)
    {"Referer": "http://localhost/calculated-columns"},      # same-site referer only
])
def test_same_origin_or_headerless_post_proceeds(client, headers):
    resp = client.post("/calculated-columns", data=FORM, headers=headers)
    assert resp.status_code == 400  # reached the route: "No file uploaded"
    assert resp.get_json()["error"] == "No file uploaded"


def test_get_requests_unaffected(client):
    assert client.get("/", headers={"Origin": "https://evil.example"}).status_code == 200


def test_cookie_flags():
    cfg = datadragon.app.config
    assert cfg["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert cfg["SESSION_COOKIE_HTTPONLY"] is True
    assert cfg["SESSION_COOKIE_SECURE"] is False  # opt in with DATADRAGON_HTTPS=1
