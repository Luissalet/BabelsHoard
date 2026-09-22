"""Browser-attack guard, static-file confinement and error shape.

These run against a real built-looking ``static_dir`` so the SPA route is
exercised: a request must never be able to read a file outside it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from babels_hoard.api import create_app

PORT = 18814


@pytest.fixture()
def spa_client(tmp_path):
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (static / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET", encoding="utf-8")
    app = create_app(tmp_path / "data", static, port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def test_spa_serves_index_and_real_files(spa_client):
    assert "spa" in spa_client.get("/").text
    assert "spa" in spa_client.get("/libraries").text  # SPA fallback
    assert spa_client.get("/favicon.svg").text == "<svg/>"
    assert spa_client.get("/assets/app.js").text == "console.log(1)"


@pytest.mark.parametrize(
    "path",
    [
        "/..%2fsecret.txt",
        "/%2e%2e/secret.txt",
        "/..%2f..%2f..%2f..%2f..%2f..%2fetc%2fpasswd",
        "//etc/passwd",
        "/%2Fetc%2Fpasswd",
        "/assets/..%2f..%2fsecret.txt",
    ],
)
def test_spa_never_serves_files_outside_static_dir(spa_client, path):
    r = spa_client.get(path)
    assert "TOP-SECRET" not in r.text
    assert "root:" not in r.text


def test_validation_errors_use_the_error_shape(spa_client):
    r = spa_client.post("/api/agent/docs_search", json={"limit": 3})
    assert r.status_code == 422
    body = r.json()
    assert body["error"] == "invalid_arguments"
    assert "query" in body["message"]


def test_unknown_api_route_uses_the_error_shape(spa_client):
    r = spa_client.post("/api/agent/no_such_tool", json={})
    assert r.status_code in (404, 405)
    assert set(r.json()) == {"error", "message"}


def test_cross_site_write_blocked_even_without_origin(spa_client):
    r = spa_client.post(
        "/api/agent/docs_index_folder", json={"path": "."}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert r.status_code == 403


def test_dns_rebinding_host_rejected_on_every_route(spa_client):
    for path in ("/", "/api/health", "/assets/app.js"):
        r = spa_client.get(path, headers={"Host": f"attacker.example:{PORT}"})
        assert r.status_code == 400


def test_cross_site_subresource_gets_to_the_api_are_blocked(spa_client):
    # <img src="http://127.0.0.1:port/api/lookup?..."> on another site would
    # otherwise make Babel index packages / run the interpreter probe.
    headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}
    assert spa_client.get("/api/lookup?symbol=json.dumps", headers=headers).status_code == 403
    assert spa_client.get("/api/jobs", headers=headers).status_code == 403
    # health stays reachable for Faustus / any tab, and so does navigation
    assert spa_client.get("/api/health", headers=headers).status_code == 200
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}
    assert spa_client.get("/api/jobs", headers=nav).status_code == 200
    assert spa_client.get("/", headers=headers).status_code == 200
