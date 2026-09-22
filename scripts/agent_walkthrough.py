"""Walk the agent use cases (docs/USE_CASES.md UC4, UC5, UC6, UC8) over real MCP stdio.

Spawns ``babels_hoard/mcp_server.py`` as a subprocess speaking the MCP stdio
protocol against an already running app, and performs each scenario as a
sequence of tool calls the way a small local model would: start from
``list_tools``, pick tools by their descriptions, chain ids and paths from
one result into the next, and react to errors. Prints every call with the
size of its result so result compactness can be judged.

Usage:
    python -m babels_hoard --port 18810 --data-dir data-uxtest/run1 --no-browser
    python scripts/agent_walkthrough.py --url http://127.0.0.1:18810 \
        --project data-uxtest/faustus-like [--transcript data-uxtest/agent.json]

The project folder must contain a virtual environment (.venv) and, for UC5,
``backend/app/chat_service.py``; see docs/USABILITY_REPORT.md for the data set.
Nothing is written into the project except UC6's ``backend/app/memory.py``
(what Faustus's own file tool would write).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = REPO_ROOT / "babels_hoard" / "mcp_server.py"
# What a Faustus-style prompt listing keeps of each tool description.
LISTING_CHARS = 120

UC4_DRAFT = '''import httpx
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/llm", tags=["llm"])
LLAMA = "http://127.0.0.1:8080/v1/chat/completions"


@router.post("/stream")
async def stream_chat(body: dict):
    async def gen():
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            async with client.stream("POST", LLAMA, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_text_lines():
                    yield line + "\\n"

    return StreamingResponse(gen(), media_type="text/event-stream", status=200)
'''

UC6_MEMORY = '''"""Semantic memory panel backed by a local fastembed model."""
from __future__ import annotations

import numpy as np
from fastembed import TextEmbedding

model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")


def embed_documents(texts: list[str]) -> np.ndarray:
    return np.stack(list(model.embed(texts, batch_size=32)))


def embed_query(text: str) -> np.ndarray:
    return next(iter(model.query_embed(text)))


def top_k(query: str, docs: list[str], k: int = 5) -> list[tuple[str, float]]:
    matrix = embed_documents(docs)
    q = embed_query(query)
    scores = matrix @ q / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(q) + 1e-9)
    order = np.argsort(-scores)[:k]
    return [(docs[i], float(scores[i])) for i in order]
'''

# Three realistic mistakes introduced into the (correct) UC5 module.
UC5_MUTATIONS = [
    ('router = APIRouter(prefix="/api", tags=["chat"])', 'router = APIRouter(prefix="/api", tag=["chat"])'),
    ("timeout=httpx.Timeout(settings.llm_timeout_s, connect=5.0),", "timeout=httpx.Timeout(settings.llm_timeout_s, connect_timeout=5.0),"),
    ("from sqlalchemy.orm import DeclarativeBase,", "from sqlalchemy.orm import DeclarativeBaseModel, DeclarativeBase,"),
]


class Agent:
    def __init__(self, session: ClientSession):
        self.session = session
        self.transcript: list[dict[str, Any]] = []
        self.images_seen = 0

    async def call(self, tool: str, **args: Any) -> tuple[bool, Any]:
        start = time.monotonic()
        res = await self.session.call_tool(tool, args)
        elapsed = time.monotonic() - start
        texts = []
        for block in res.content:
            if block.type == "text":
                texts.append(block.text)
            else:
                self.images_seen += 1
                texts.append(f"<{block.type} block>")
        raw = "\n".join(texts)
        try:
            data: Any = json.loads(raw) if not res.isError else raw
        except json.JSONDecodeError:
            data = raw
        shown = {k: (v if len(str(v)) < 60 else str(v)[:57] + "...") for k, v in args.items()}
        flag = "ERROR " if res.isError else ""
        print(f"  -> {tool}({shown})\n     <- {flag}{len(raw):,} chars (~{len(raw) // 4:,} tokens), {elapsed:.2f}s")
        if res.isError:
            print(f"        {raw[:300]}")
        self.transcript.append(
            {"tool": tool, "args": shown, "error": bool(res.isError), "chars": len(raw), "seconds": round(elapsed, 2),
             "result": data if len(raw) < 6000 else raw[:6000]}
        )
        return (not res.isError), data


def summarize_findings(result: dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return str(result)[:200]
    lines = [f"ok={result.get('ok')} checked={result.get('checked')} unchecked={result.get('unchecked')}"]
    for f in result.get("findings", []):
        sugg = f" (suggestion: {f['suggestion']})" if f.get("suggestion") else ""
        lines.append(f"        line {f['line']}: {f['severity']} {f['code']}: {f['message']}{sugg}")
    return "\n".join(lines)


async def run(url: str, project: Path, transcript_path: Path | None) -> int:
    env = dict(os.environ)
    env["BABEL_URL"] = url
    params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER)], env=env)
    problems: list[str] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"server instructions: {len(init.instructions or '')} chars")
            agent = Agent(session)

            print("\n== list_tools: what a prompt listing that keeps the first line (120 chars) shows")
            tools = (await session.list_tools()).tools
            for tool in tools:
                first = (tool.description or "").strip().splitlines()[0]
                seen = first[:LISTING_CHARS]
                spanish = any(w in seen.lower() for w in ("buscar", "firma", "comprobar", "registrar", "leer", "listar", "indexar", "instalar", "catalogo", "catálogo", "documentación", "documentacion"))
                print(f"  {tool.name:22s} [{len(first):3d} chars{', ES' if spanish else ', no ES'}] {seen}")
                if len(first) > 110 or not spanish:
                    problems.append(f"{tool.name}: first description line is {len(first)} chars, Spanish trigger words: {spanish}")

            # ---------------------------------------------------------- UC4
            print("\n== UC4: write an httpx streaming endpoint and prove every API exists")
            ok, libs = await agent.call("docs_libraries")
            env_id = None
            if ok:
                for e in libs.get("environments", []):
                    # The project's Python environment (a frontend-only env has no python_path).
                    if (e.get("python_path") or "").startswith(str(project)):
                        env_id = e["id"]
            if env_id is None:
                ok, reg = await agent.call("docs_add_environment", path=str(project))
                if not ok:
                    problems.append("UC4: could not register the project")
                    return 1
                env_id = reg["environment"]["id"]
                print(f"     registered {env_id}; dependency job {reg.get('dependency_job_id')}: {reg.get('message')}")
            ok, look = await agent.call("api_lookup", symbol="httpx.AsyncClient.stream", env=env_id)
            if ok:
                print(f"     found={look.get('found')} signature={str(look.get('signature'))[:120]}")
            ok, chk = await agent.call("api_check_code", code=UC4_DRAFT, env=env_id)
            print("     " + summarize_findings(chk))
            fixed = UC4_DRAFT
            for f in chk.get("findings", []) if ok else []:
                if f["code"] == "unknown_attribute" and f.get("suggestion"):
                    bad = f["symbol"].rsplit(".", 1)[-1]
                    fixed = fixed.replace(bad, f["suggestion"])
                elif f["code"] == "unexpected_keyword":
                    ok2, sig = await agent.call("api_lookup", symbol="fastapi.responses.StreamingResponse", env=env_id)
                    names = [p["name"] for p in sig.get("params", [])] if ok2 else []
                    print(f"     StreamingResponse params: {names}")
                    if "status_code" in names:
                        fixed = fixed.replace("status=200", "status_code=200")
            ok, chk2 = await agent.call("api_check_code", code=fixed, env=env_id)
            print("     after fix: " + summarize_findings(chk2))
            if not (ok and chk2.get("ok") and not chk2.get("findings")):
                problems.append("UC4: the fixed endpoint is not clean")

            # ---------------------------------------------------------- UC5
            print("\n== UC5: review a correct 300+ line module, then one with three mistakes")
            module = (project / "backend" / "app" / "chat_service.py").read_text(encoding="utf-8")
            print(f"     module: {len(module.splitlines())} lines")
            ok, clean = await agent.call("api_check_code", code=module, env=env_id)
            print("     " + summarize_findings(clean))
            if not ok or clean.get("findings"):
                problems.append(f"UC5: correct module produced findings: {summarize_findings(clean)}")
            broken = module
            for old, new in UC5_MUTATIONS:
                assert old in broken, old
                broken = broken.replace(old, new)
            ok, bad = await agent.call("api_check_code", code=broken, env=env_id)
            print("     mutated: " + summarize_findings(bad))
            caught = len([f for f in bad.get("findings", []) if f["severity"] == "error"]) if ok else 0
            if caught != len(UC5_MUTATIONS):
                problems.append(f"UC5: {caught} of {len(UC5_MUTATIONS)} introduced mistakes reported as errors")

            # ---------------------------------------------------------- UC6
            print("\n== UC6: learn fastembed, write memory.py (Faustus file tool), check it")
            ok, hits = await agent.call("docs_search", query="query embedding", library="fastembed", env=env_id)
            if ok:
                print(f"     {hits.get('count')} hits; first: {[h['qualname'] for h in hits.get('results', [])[:3]]}; "
                      f"other keys: {[k for k in hits if k not in ('results', 'query', 'count', 'truncated')]}")
                if not hits.get("results"):
                    problems.append("UC6: search in a not-yet-indexed package returns nothing and says nothing about why")
            ok, look = await agent.call("api_lookup", symbol="fastembed.TextEmbedding.query_embed", env=env_id)
            if ok:
                print(f"     found={look.get('found')} signature={str(look.get('signature'))[:140]}")
            ok, hits = await agent.call("docs_search", query="query embedding", library="fastembed", env=env_id)
            if ok:
                print(f"     after lookup: {hits.get('count')} hits; first: {[h['qualname'] for h in hits.get('results', [])[:3]]}")
            target = project / "backend" / "app" / "memory.py"
            target.write_text(UC6_MEMORY, encoding="utf-8")  # Faustus's own file tool
            print(f"     (Faustus file tool) wrote {target.name}, {len(UC6_MEMORY.splitlines())} lines")
            ok, chk = await agent.call("api_check_code", code=target.read_text(encoding="utf-8"), env=env_id)
            print("     " + summarize_findings(chk))
            if not ok or chk.get("findings"):
                problems.append("UC6: memory.py (correct) produced findings")

            # ---------------------------------------------------------- UC8
            print("\n== UC8: index the project's docs folder and search it")
            ok, idx = await agent.call("docs_index_folder", path=str(project / "docs"), name="workspace-docs")
            if ok:
                print(f"     {idx.get('note')}")
            ok, hits = await agent.call("docs_search", query="start the backend", ecosystem="markdown")
            if ok and hits.get("results"):
                first = hits["results"][0]
                print(f"     first hit: {first['qualname']}")
                ok, page = await agent.call("docs_read", id=first["id"])
                if ok:
                    print(f"     read {page.get('total_chars')} chars: {page.get('text', '')[:80]!r}")

            # ------------------------------------------------ error handling
            print("\n== errors: does each one say what to do next?")
            await agent.call("api_lookup", symbol="httpx.Client.gett", env=env_id)
            await agent.call("api_lookup", symbol="httpx.Client.get", env="C:\\no\\such\\project")
            await agent.call("docs_read", id="lib-0000000000000000:nothing")
            await agent.call("docs_index_folder", path=str(project / "missing-docs"))
            await agent.call("api_check_code", code="def broken(:\n    pass\n", env=env_id)
            await agent.call("docs_add_environment", path=str(project / "README-does-not-exist.txt"))
            await agent.call("docs_install_docset", slug="not a slug")

            total = sum(c["chars"] for c in agent.transcript)
            biggest = max(agent.transcript, key=lambda c: c["chars"])
            print(f"\n{len(agent.transcript)} calls, {total:,} chars in results (~{total // 4:,} tokens); "
                  f"largest: {biggest['tool']} {biggest['chars']:,} chars; image blocks: {agent.images_seen}")
            if agent.images_seen:
                problems.append(f"{agent.images_seen} image blocks returned without being asked for")
            if transcript_path:
                transcript_path.write_text(json.dumps(agent.transcript, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"transcript: {transcript_path}")

    print("\n== problems")
    for p in problems:
        print(f"  - {p}")
    if not problems:
        print("  none")
    return 1 if problems else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:8811")
    ap.add_argument("--project", type=Path, default=REPO_ROOT / "data-uxtest" / "faustus-like")
    ap.add_argument("--transcript", type=Path, default=None)
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args.url, args.project.resolve(), args.transcript)))


if __name__ == "__main__":
    main()
