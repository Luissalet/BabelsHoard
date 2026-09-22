"""The proof the plugin actually works: spawn the real app on a free port,
then spawn the MCP adapter as a real subprocess talking stdio, and drive it
through the MCP client protocol - not by importing mcp_server's functions
directly."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from babels_hoard.api import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = REPO_ROOT / "babels_hoard" / "mcp_server.py"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _LiveApp:
    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        app = create_app(tmp_path, None, port=self.port)
        config = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=lambda: asyncio.run(self.server.serve()), daemon=True)

    def start(self) -> None:
        self.thread.start()
        for _ in range(100):
            try:
                r = httpx.get(f"http://127.0.0.1:{self.port}/api/health", timeout=1)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.1)
        raise RuntimeError("app did not become healthy in time")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture()
def live_app(tmp_path):
    app = _LiveApp(tmp_path)
    app.start()
    yield app
    app.stop()


@pytest.mark.asyncio
async def test_mcp_stdio_list_and_call_tools(live_app):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["BABEL_URL"] = f"http://127.0.0.1:{live_app.port}"
    params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert names == {
                "docs_libraries",
                "docs_search",
                "api_lookup",
                "api_check_code",
                "docs_read",
                "docs_add_environment",
                "docs_catalog",
                "docs_install_docset",
                "docs_index_folder",
            }

            reg = await session.call_tool("docs_add_environment", {"path": sys.executable, "index_dependencies": False})
            reg_body = json.loads(reg.content[0].text)
            assert reg_body["environment"]["python_path"] == sys.executable

            lookup = await session.call_tool("api_lookup", {"symbol": "json.dumps"})
            body = json.loads(lookup.content[0].text)
            assert body["found"] is True
            assert body["kind"] == "function"

            check = await session.call_tool(
                "api_check_code", {"code": "import json\njson.this_does_not_exist()\n"}
            )
            check_body = json.loads(check.content[0].text)
            assert check_body["ok"] is False
            assert check_body["findings"][0]["code"] == "unknown_attribute"


@pytest.mark.asyncio
async def test_mcp_reports_clear_error_when_app_not_running():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["BABEL_URL"] = f"http://127.0.0.1:{_free_port()}"  # nothing listening here
    params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)], env=env)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("docs_libraries", {})
            assert result.isError is True
            assert "babel_unavailable" in result.content[0].text


@pytest.mark.asyncio
async def test_mcp_tool_contract_for_a_local_model(live_app, tmp_path):
    """Every tool is described for retrieval (English + Spanish keywords),
    annotated honestly, returns stable ids that round-trip, and passes the
    app's actionable error text through."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["BABEL_URL"] = f"http://127.0.0.1:{live_app.port}"
    params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert "not instructions" in (init.instructions or "")
            tools = {t.name: t for t in (await session.list_tools()).tools}
            for name, tool in tools.items():
                # A tool listing may keep only the first line (cut at 120 characters): it
                # must be a whole statement with English and Spanish trigger words.
                first = tool.description.strip().splitlines()[0]
                assert len(first) <= 110 and first.endswith(")") and " (" in first, (name, first)
                assert any(w in first for w in ("librerías", "buscar", "firma", "comprobar", "leer", "registrar", "catálogo", "instalar", "indexar")), (name, first)
                assert "Keywords:" in tool.description, name
                keywords = tool.description.split("Keywords:", 1)[1]
                assert any(w in keywords for w in ("buscar", "firma", "comprobar", "leer", "listar", "registrar", "catalogo", "instalar", "indexar")), name
                ann = tool.annotations
                assert ann is not None and ann.destructiveHint is False, name
            network = {n for n, t in tools.items() if t.annotations.openWorldHint}
            assert network == {"docs_catalog", "docs_install_docset"}
            writers = {n for n, t in tools.items() if not t.annotations.readOnlyHint}
            assert writers == {"docs_add_environment", "docs_install_docset", "docs_index_folder"}

            await session.call_tool("docs_add_environment", {"path": sys.executable, "index_dependencies": False})
            found = json.loads((await session.call_tool("api_lookup", {"symbol": "json.dumps"})).content[0].text)
            assert found["params"] and all("name" in p for p in found["params"])
            read = json.loads((await session.call_tool("docs_read", {"id": found["id"], "max_chars": 200})).content[0].text)
            assert read["found"] is True and read["qualname"] == "json.dumps"

            libs = json.loads((await session.call_tool("docs_libraries", {})).content[0].text)
            assert any(e["default"] for e in libs["environments"])
            assert all(set(lib) <= {"id", "ecosystem", "name", "version", "env", "status", "entries"} for lib in libs["libraries"])

            bad = await session.call_tool("docs_index_folder", {"path": str(tmp_path / "missing")})
            assert bad.isError is True
            assert "bad_path" in bad.content[0].text and "not a directory" in bad.content[0].text

            invalid = await session.call_tool("api_check_code", {"code": "x = 1", "language": "cobol"})
            assert invalid.isError is True and "unsupported_language" in invalid.content[0].text


@pytest.mark.asyncio
async def test_mcp_refuses_non_loopback_url_with_a_clear_message():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env["BABEL_URL"] = "http://example.com:8811"
    params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("docs_libraries", {})
            assert result.isError is True
            assert "babel_misconfigured" in result.content[0].text
