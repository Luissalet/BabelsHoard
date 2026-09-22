import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from babels_hoard.api import create_app

PORT = 18813


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path, None, port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "babels-hoard"
    assert body["name"] == "Babel's Hoard"
    assert body["status"] == "ok"


def test_no_ui_placeholder_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "npm run build" in r.text


def test_bad_host_rejected(client):
    r = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 400
    assert r.json()["error"] == "bad_host"


def test_cross_site_post_rejected(client):
    r = client.post(
        "/api/agent/docs_search", json={"query": "x"}, headers={"sec-fetch-site": "cross-site"}
    )
    assert r.status_code == 403
    assert r.json()["error"] == "cross_site_blocked"


def test_cross_origin_post_rejected(client):
    r = client.post(
        "/api/agent/docs_search",
        json={"query": "x"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert r.status_code == 403


def test_same_origin_post_allowed(client):
    r = client.post(
        "/api/agent/docs_search",
        json={"query": "x"},
        headers={"Origin": f"http://127.0.0.1:{PORT}"},
    )
    assert r.status_code == 200


def test_plain_get_navigation_always_works(client):
    r = client.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200


def test_agent_register_environment_and_lookup(client):
    r = client.post(
        "/api/agent/docs_add_environment",
        json={"path": sys.executable, "index_dependencies": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["environment"]["python_path"] == sys.executable

    r2 = client.post("/api/agent/api_lookup", json={"symbol": "json.dumps"})
    assert r2.status_code == 200
    assert r2.json()["found"] is True


def test_agent_check_code_flags_bad_call(client):
    client.post("/api/agent/docs_add_environment", json={"path": sys.executable, "index_dependencies": False})
    r = client.post(
        "/api/agent/api_check_code",
        json={"code": "import os\nos.this_is_fake()\n"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["findings"][0]["code"] == "unknown_attribute"


def test_agent_docs_read_not_found(client):
    r = client.post("/api/agent/docs_read", json={"id": "nope"})
    assert r.status_code == 200
    assert r.json()["found"] is False


def test_agent_docs_index_folder(client, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# Title\nBody text.\n", encoding="utf-8")
    r = client.post("/api/agent/docs_index_folder", json={"path": str(docs), "name": "Test Docs"})
    assert r.status_code == 200
    assert r.json()["status"] == "done"


def test_agent_call_is_logged(client):
    client.post("/api/agent/docs_search", json={"query": "x"})
    r = client.get("/api/agent_calls")
    assert r.status_code == 200
    calls = r.json()
    assert any(c["tool"] == "docs_search" for c in calls)


def test_error_shape_for_unknown_environment(client):
    r = client.post(
        "/api/libraries/index", json={"env": "no-such-env", "import_name": "x"}
    )
    assert r.status_code == 404
    body = r.json()
    assert set(body.keys()) == {"error", "message"}


def test_ui_search_endpoint(client):
    r = client.post(
        "/api/agent/docs_add_environment", json={"path": sys.executable, "index_dependencies": False}
    )
    env_id = r.json()["environment"]["id"]
    r2 = client.post("/api/libraries/index", json={"env": env_id, "import_name": "json"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "done"

    r3 = client.get("/api/search", params={"q": "dumps"})
    assert r3.status_code == 200
    assert r3.json()["count"] >= 1

    r4 = client.get("/api/libraries")
    assert any(lib["name"] == "stdlib/json" for lib in r4.json())

    r5 = client.get("/api/environments")
    assert any(e["id"] == env_id for e in r5.json())

    r6 = client.get("/api/jobs")
    assert isinstance(r6.json(), list)
