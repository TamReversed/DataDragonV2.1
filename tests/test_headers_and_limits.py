"""T3.8: per-route rate limits, proxy awareness (opt-in), security headers."""
import importlib
import os
import sys

import pytest

import datadragon
from helpers import make_csv


def test_security_headers_are_on_pages_and_json_and_errors(client):
    for response in (client.get("/"), client.get("/healthz"), client.get("/nope-404")):
        headers = response.headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "same-origin"
        csp = headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
        assert "https://cdn.jsdelivr.net" in csp and "https://cdnjs.cloudflare.com" in csp


def test_csp_only_names_the_hosts_the_pages_load_code_from():
    import glob
    import re
    hosts = set()
    for path in glob.glob("templates/*.html"):
        hosts |= set(re.findall(r"https://([\w.-]+)/", " ".join(re.findall(r"<script\b[^>]*src=\"(https://[^\"]+)\"", open(path, encoding="utf-8").read()))))
    for host in hosts:
        assert "https://" + host in datadragon.CONTENT_SECURITY_POLICY, host


def test_hitting_one_route_does_not_use_up_another_routes_allowance(client, tmp_path):
    path = make_csv(tmp_path / "a.csv", "a\n1\n")
    codes = []
    for _ in range(22):                                                  # find-replace allows 20 a minute
        with open(path, "rb") as fh:
            codes.append(client.post("/find-replace", data={"file": (fh, "a.csv"), "find_text": "1"},
                                     content_type="multipart/form-data").status_code)
    assert codes[-1] == 429 and 429 not in codes[:20]
    with open(path, "rb") as fh:                                         # another route still has its own allowance
        resp = client.post("/get-columns", data={"file": (fh, "a.csv")}, content_type="multipart/form-data")
    assert resp.status_code == 200


def test_empty_and_idle_buckets_are_evicted():
    now = 1_000_000.0
    datadragon.rate_limit_store[("1.1.1.1", "x")] = [now - 500]
    datadragon.rate_limit_store[("2.2.2.2", "x")] = [now - 5]
    datadragon.rate_limit_store[("3.3.3.3", "x")] = []
    datadragon.cleanup_rate_limits(now)
    assert set(datadragon.rate_limit_store) == {("2.2.2.2", "x")}


def test_x_forwarded_headers_are_ignored_unless_the_proxy_is_trusted(client):
    response = client.get("/healthz", headers={"X-Forwarded-For": "9.9.9.9", "X-Forwarded-Host": "evil.example"})
    assert response.status_code == 200
    assert "ProxyFix" not in type(datadragon.app.wsgi_app).__name__


def test_with_the_proxy_setting_forwarded_host_and_address_are_used():
    """Load a second copy of the app with DATADRAGON_TRUST_PROXY=1 (its own module, so the other tests are untouched)."""
    import importlib.util
    os.environ["DATADRAGON_TRUST_PROXY"] = "1"
    try:
        spec = importlib.util.spec_from_file_location("datadragon_proxy_copy", "datadragon.py")
        copy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(copy)
    finally:
        del os.environ["DATADRAGON_TRUST_PROXY"]
    assert "ProxyFix" in type(copy.app.wsgi_app).__name__
    client = copy.app.test_client()
    seen = {}

    @copy.app.route("/_whoami")
    def whoami():
        from flask import request, jsonify
        return jsonify({"ip": request.remote_addr, "host": request.host})

    body = client.get("/_whoami", headers={"X-Forwarded-For": "9.9.9.9", "X-Forwarded-Host": "tools.example.org"}).get_json()
    assert body == {"ip": "9.9.9.9", "host": "tools.example.org"}
