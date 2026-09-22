# MCP tools

Babel's Hoard exposes a stdio MCP server (`babels_hoard/mcp_server.py`). It
talks to the running app over `BABEL_URL` (default `http://127.0.0.1:8811`,
loopback only) and refuses to start against a non-loopback URL. Every tool's
body is a thin wrapper over `POST /api/agent/<tool>`, so behaviour is
identical whether you call it through MCP or through the HTTP API directly
(see `tests/test_api.py` and `tests/test_mcp_protocol.py`).

All results are JSON. Strings are truncated with an explicit cap; list
results carry `count`/`truncated` (and, for docsets, `total`). Nothing here
executes user code - indexing is static (AST/type-declarations), and
`api_check_code` only ever parses.

## `docs_libraries(ecosystem?, env?)`

Read-only, idempotent. What is indexed/installed, with exact versions, plus
the registered environments.

- `ecosystem`: `"python" | "js" | "docset" | "markdown"`, optional filter.
- `env`: environment id, optional filter.

```json
{"libraries": [{"id": "lib-...", "ecosystem": "python", "name": "httpx",
  "version": "0.28.1", "source": "env:env-...", "status": "done",
  "entry_count": 372}], "environments": [{"id": "env-...", "label": "...",
  "python_path": "...", "python_version": "3.11.15"}]}
```

## `docs_search(query, library?, ecosystem?, kind?, env?, limit=8)`

Read-only, idempotent. Ranked full-text search (SQLite FTS5, bm25) across
every indexed API, docset page and markdown section. `query` words are also
split on `camelCase`/`snake_case`/`.` so `"get request"` matches
`httpx.Client.get`. `limit` is capped at 50.

```json
{"query": "send a get request", "results": [{"id": "lib-...:httpx.Client.get",
  "qualname": "httpx.Client.get", "kind": "method", "library": "httpx@0.28.1",
  "signature": "get(self, url: ...) -> Response", "summary": "Send a `GET` request.",
  "score": 8.4}], "count": 5, "truncated": false}
```

## `api_lookup(symbol, env?, library?)`

Read-only (writes only a local index cache on first use), idempotent.
Exact signature/params/returns/doc for one dotted symbol, from the version
actually installed. Indexes the owning package lazily if needed.

- On success: `{found: true, signature, params: [{name, kind, annotation,
  default, description}], returns, doc, deprecated, deprecated_note,
  source_path, source_line, library, env_id}`.
- On failure: `{found: false, suggestions: [...], message}` - suggestions are
  the closest real names (difflib over siblings), so the caller can retry
  with something that actually exists instead of guessing again.

```json
{"symbol": "httpx.Client.gett", "found": false, "suggestions": ["get"],
 "message": "'httpx.Client.gett' does not exist in httpx installed in ..."}
```

## `api_check_code(code, env?, language="python")`

Read-only (writes only a local index cache on first use), idempotent.
Parses `code` with `ast` (or a regex-based import check for
`language="typescript"`, see boundary below) and reports, conservatively:

| code | meaning |
| --- | --- |
| `syntax_error` | the snippet does not parse |
| `unknown_module` | an imported dotted path does not exist in the environment |
| `unknown_attribute` | an attribute/call target does not exist on a resolved module/class/instance |
| `unexpected_keyword` | a keyword argument is not a parameter and there is no `**kwargs` |
| `too_many_positional` | more positional args than the callee accepts and no `*args` |
| `missing_required` | a required parameter is not supplied (only when the call has no `*args`/`**kwargs`/unpacking) |
| `deprecated` | (warning) the target is marked deprecated |

Design goal: **zero false positives over coverage**. Anything Babel cannot
resolve with confidence (dynamic attribute access, an unresolved base class,
a conditional cross-module alias like `os.path`, an untyped value) is
counted in `unchecked` and left silent rather than guessed.

```json
{"ok": false, "findings": [{"line": 3, "col": 0, "severity": "error",
  "code": "unexpected_keyword", "symbol": "c.get(totally_fake_kw=...)",
  "message": "'totally_fake_kw' is not a parameter of 'c.get'."}],
 "checked": 4, "unchecked": 1, "libraries": ["httpx@0.28.1"]}
```

## `docs_read(id, offset=0, max_chars=4000)`

Read-only, idempotent. Reads a docset page/section or a full docstring in
chunks. `max_chars` capped at 20000.

```json
{"found": true, "text": "...", "offset": 0, "total_chars": 5200,
 "has_more": true, "next_offset": 4000}
```

## `docs_add_environment(path, index_dependencies=true)`

Not read-only, idempotent. Registers a project directory (auto-detects
`.venv`/`venv`/`env` and `node_modules`), a specific interpreter, or a
`node_modules` folder. If `index_dependencies` is true, queues indexing of
the project's direct dependencies (read from `pyproject.toml` /
`requirements*.txt`) as a background job and returns its id; the tool
itself returns immediately with the registered environment.

```json
{"environment": {"id": "env-...", "python_path": "...", "python_version": "3.13.1"},
 "dependency_job_id": "job-..."}
```

## `docs_catalog(query="", limit=10)`

Read-only, idempotent, **network** (the DevDocs mirror catalogue).
`limit` capped at 50.

```json
{"results": [{"slug": "python~3.12", "name": "Python", "version": "3.12",
  "db_size_kb": 4312}], "count": 1, "total": 1, "truncated": false}
```

## `docs_install_docset(slug)`

Not read-only, idempotent, **network** - the only tool that reaches the
internet besides the catalogue above. Downloads, converts to Markdown and
indexes one docset by section anchor. Returns the resulting library row.

## `docs_index_folder(path, name?)`

Not read-only, idempotent. Indexes a folder of `.md`/`.mdx`/`.rst`/`.txt`
files by heading as a `markdown` library.

## Any MCP client (not just Faustus)

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

On Windows, `command` is `.venv\Scripts\python.exe`.

## Errors

An unreachable app raises `babel_unavailable: Babel's Hoard is not running...`.
A 4xx from the API is passed through as the tool's error message (e.g. an
unknown docset slug, a path that does not exist).
