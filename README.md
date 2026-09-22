# Babel's Hoard
### What does the version on disk actually say?
**A local, version-exact API index for your Python and JS/TS projects, and a hallucinated-API checker built on top of it - so a coding model stops guessing signatures from stale training data.**

[Español](README.es.md) · [Run locally](#run-locally-on-windows) · [Connect an AI](docs/MCP.md) · [Portfolio](https://luissalet.github.io/Portfolio/#projects)

![Search view: querying "send a get request" surfaces httpx.Client.get with its real, installed signature](docs/media/search.png)
*Actual application, demo data (Babel's own interpreter, indexed at build time).*

## Why

A local coding model's training data is a blur of library versions: it
writes `df.append(...)` for pandas 2.x, passes a renamed keyword argument,
imports a symbol that moved modules, or invents a parameter that never
existed. Web search is slow, noisy, and not specific to the version you
actually have installed. The ground truth is already on disk - the
project's own `.venv` and `node_modules` - Babel reads it, indexes it
statically, and answers with the real signature, or tells you plainly that
what you asked for does not exist and what the closest real name is.

![Check code view: a snippet calling client.get(..., bogus_kw=1) is flagged as an unexpected keyword argument](docs/media/check-code.png)
*Actual application, demo data. The checker never executes the snippet - it parses it and resolves names against the indexed environment.*

## What is implemented

| Area | Available now | Boundary |
| --- | --- | --- |
| Python indexing | Static (griffe/AST, never executes project code) indexing of any registered interpreter's installed distributions and the stdlib, lazily and cached per (env, package, version) | Capped at 1500 entries and depth 8 per package (marked `partial`, not silently truncated); compiled extensions with no `.pyi` stubs are recorded as `note: "compiled, no stubs"` rather than invented; a conditional cross-module alias griffe cannot statically resolve (`os.path` is the canonical example) is recorded as an unresolved marker, not fully described |
| JS/TS indexing | TypeScript compiler API over a package's `.d.ts` (types/typings field, `exports[...].types`, `index.d.ts`, or an `@types/<name>` package); functions, classes, interfaces, one level of members, JSDoc, `@deprecated` | No Node.js on PATH degrades gracefully to a clear error, the rest of the app keeps working; only exported top-level members plus one member level are indexed |
| Hallucination / misuse check (`api_check_code`) | Unknown imports/attributes, unexpected keyword args, too-many-positional, missing-required, deprecated - all with "did you mean" suggestions; zero-false-positive design, proven against a two-version fixture package (an API removed between versions) and against real httpx known-good/known-bad snippets | Python only for full checks; TypeScript checking is v1 (named-import existence only); dynamic code, unresolved base classes and untyped values are reported as `unchecked`, not silently assumed correct |
| Full-text search | SQLite FTS5, bm25-weighted (name/qualname/signature/summary/doc), camelCase/snake_case/dotted-aware tokenization, filters by library/ecosystem/kind/env | No semantic/embedding search - lexical only |
| Offline docsets | DevDocs mirror catalogue + install (HTML converted to Markdown, split by section anchor, indexed) | Network only for catalogue/install, nothing else; conversion is a small stdlib-only HTML→Markdown pass, not pixel-perfect |
| Markdown folders | Index any folder of `.md`/`.mdx`/`.rst`/`.txt` split by heading | No cross-file link resolution |
| Assistant audit | Every `/api/agent/*` call is logged (tool, args summary, duration, ok/error) and shown in "Assistant activity" | Local only, not exported |
| UI | React 19 desktop-style app: Search, Libraries (register environments, index on demand, index project dependencies as a background job), Check code, Docsets, Assistant activity, Settings; light/dark, English/Spanish | - |

## Connect it to Faustus

The app declares itself with `faustus-plugin.json`. Start Babel's Hoard,
then in Faustus: **Connectors → Nearby apps → Add**.

| MCP tool | Read-only | What |
| --- | --- | --- |
| `docs_libraries` | yes | What is indexed, with versions, and the registered environments |
| `docs_search` | yes | Ranked search across APIs, docsets and markdown |
| `api_lookup` | yes* | Exact signature/params/doc for one symbol (lazily indexes on first use) |
| `api_check_code` | yes* | Find hallucinated/removed/misused APIs in a snippet |
| `docs_read` | yes | Read a docset section or full docstring, in chunks |
| `docs_add_environment` | no | Register a project/interpreter/node_modules |
| `docs_catalog` | yes | List installable offline docsets (network) |
| `docs_install_docset` | no | Download + index a docset (network) |
| `docs_index_folder` | no | Index a folder of markdown docs |

Full argument/return shapes, limits and examples: [docs/MCP.md](docs/MCP.md).

It also works with any MCP client over stdio, not just Faustus:

```json
{
  "mcpServers": {
    "babels-hoard": {
      "command": "/absolute/path/to/babels-hoard/.venv/bin/python",
      "args": ["/absolute/path/to/babels-hoard/babels_hoard/mcp_server.py"],
      "env": { "BABEL_URL": "http://127.0.0.1:8811" }
    }
  }
}
```

## Run locally on Windows

Double-click **`Iniciar Babel's Hoard.cmd`** (creates the venv, installs
dependencies, builds the frontend on first run, and starts the server), or
manually:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-lock.txt
cd frontend; npm ci; npm run build; cd ..
.venv\Scripts\python -m babels_hoard
```

Open <http://127.0.0.1:8811>. Use `--demo` to run against synthetic data in
`data-demo/` (registers Babel's own interpreter and indexes a handful of
real packages, so the UI has real signatures to show without touching your
projects) and `--port <p>` / `--data-dir <path>` to override defaults.
Stop with **`Detener Babel's Hoard.cmd`**.

## Architecture

FastAPI + SQLite (WAL, FTS5) backend, React 19 + Vite frontend, a stdio MCP
adapter, and a background job worker for long-running indexing. Details,
data model and the non-obvious static-analysis decisions:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

```
.venv/bin/python -m pytest -q
```

**48 tests, ~12-14 s, offline by default** (a live-network docset install
against the real DevDocs mirror was verified manually, not in CI):
environment detection/probing, Python indexing against real installed
packages (httpx, the stdlib) and a purpose-built two-version fixture
package (`fakelib` 1.x/2.x, modeling a pandas-style `append` → `concat`
migration), the checker's every rule (including the alias-resolution edge
case that used to be a false positive), FTS5 search, docset install from a
tiny fixture, markdown folder indexing, JS/TS indexing against a fixture
`.d.ts` package, the full HTTP API and its browser-attack guard through
FastAPI's `TestClient`, and the MCP adapter driven **through the real MCP
stdio protocol** against a live instance of the app (not by importing its
functions).

`npm run build` in `frontend/` passes with zero TypeScript errors.

## Privacy and limits

Binds `127.0.0.1` only, no telemetry. The only two tools that reach the
network are `docs_catalog` and `docs_install_docset` (the DevDocs mirror);
everything else - probing interpreters, indexing packages, searching,
checking code - is entirely local. Source code is read to build the index
(paths, signatures, docstrings) but user project code is never executed,
only parsed statically.
