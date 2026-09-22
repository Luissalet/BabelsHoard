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
                "api_check_code", {"code": "import os\nos.this_does_not_exist()\n"}
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
