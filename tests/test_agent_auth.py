"""The per-tool routes /api/agent/<tool> need the same bearer token as POST /api/agent/call."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from babels_hoard.api import create_app

PORT = 18815


@pytest.fixture()
def app(tmp_path):
    return create_app(tmp_path / "data", None, port=PORT)


@pytest.fixture()
def anonymous(app):
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def _token(app) -> str:
    return (app.state.data_dir / "mcp-token").read_text(encoding="utf-8").strip()


def test_tool_route_without_a_token_is_refused(anonymous):
    r = anonymous.post("/api/agent/docs_libraries", json={})
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_tool_route_with_a_wrong_token_is_refused(anonymous):
    r = anonymous.post("/api/agent/docs_libraries", json={}, headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_tool_route_with_the_token_works_and_the_scheme_is_case_insensitive(app, anonymous):
    for scheme in ("Bearer", "bearer"):
        r = anonymous.post("/api/agent/docs_libraries", json={}, headers={"Authorization": f"{scheme} {_token(app)}"})
        assert r.status_code == 200, r.text


def test_every_tool_route_is_protected(app, anonymous):
    tools = [t["name"] for t in anonymous.get("/api/agent/tools").json()["tools"]]
    assert len(tools) >= 10
    for name in tools:
        assert anonymous.post(f"/api/agent/{name}", json={}).status_code == 401, name


def test_a_destructive_tool_does_not_run_without_the_token(anonymous, tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.md").write_text("# A\ntext", encoding="utf-8")
    assert anonymous.post("/api/agent/docs_index_folder", json={"path": str(folder)}).status_code == 401
    assert anonymous.get("/api/health").json()["libraries"] == 0


def test_the_catalogue_and_the_ui_routes_stay_open(anonymous):
    assert anonymous.get("/api/agent/tools").status_code == 200
    assert anonymous.get("/api/jobs").status_code == 200
    assert anonymous.get("/api/agent_calls").status_code == 200


def test_the_token_is_stable_across_app_starts(tmp_path):
    first = create_app(tmp_path / "data", None, port=PORT)
    token = _token(first)
    second = create_app(tmp_path / "data", None, port=PORT)
    assert _token(second) == token
