"""Test helper: the headers the MCP adapter sends (the app requires its bearer token on every /api/agent/<tool>)."""
from __future__ import annotations


def agent_headers(app) -> dict[str, str]:
    token = (app.state.data_dir / "mcp-token").read_text(encoding="utf-8").strip()
    return {"Authorization": f"Bearer {token}"}
