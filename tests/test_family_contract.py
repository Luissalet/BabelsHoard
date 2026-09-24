"""The shared family contract (Hoard Link 0.4) on top of the per-tool routes:
GET /api/agent/tools lists exactly the tools the MCP adapter exposes, and
POST /api/agent/call dispatches them behind the bearer token in
data/mcp-token — while every /api/agent/<tool> route keeps working."""

from pathlib import Path

from fastapi.testclient import TestClient

from babels_hoard.api import create_app
from babels_hoard.hoard_link import family

PORT = 8811
MCP_SOURCE = Path(__file__).resolve().parents[1] / "babels_hoard" / "mcp_server.py"


def _client(tmp_path):
    app = create_app(tmp_path / 'data', None, port=PORT)
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


def test_catalogue_matches_the_mcp_adapter(tmp_path):
    with _client(tmp_path) as c:
        cat = c.get("/api/agent/tools").json()
        names = sorted(t["name"] for t in cat["tools"])
        expected = sorted(k for k in family.descriptions_from_fastmcp_source(str(MCP_SOURCE)) if not k.startswith("__"))
        assert names == expected
        assert all(t["description"] for t in cat["tools"])
        assert all(t["inputSchema"]["type"] == "object" for t in cat["tools"])
        assert cat["instructions"]
        health = c.get("/api/health").json()
        assert health["hoard_link"]["family"] == family.FAMILY_VERSION


def test_call_needs_the_token_and_dispatches(tmp_path):
    with _client(tmp_path) as c:
        token_file = tmp_path / "data" / "mcp-token"
        assert token_file.is_file() and token_file.read_text().strip()
        first = c.get("/api/agent/tools").json()["tools"][0]["name"]
        assert c.post("/api/agent/call", json={"name": first, "arguments": {}}).status_code == 401
        headers = {"Authorization": "Bearer " + token_file.read_text().strip()}
        r = c.post("/api/agent/call", json={"name": "no_such_tool", "arguments": {}}, headers=headers)
        assert r.status_code == 404 and first in r.json()["tools"]
        r = c.post("/api/agent/call", json={"name": first, "arguments": {}}, headers=headers)
        # Whatever the tool answers (some need arguments), it is the tool that answered — not the dispatcher.
        assert r.status_code in (200, 400, 404, 422)
        assert r.headers["content-type"].startswith("application/json")
