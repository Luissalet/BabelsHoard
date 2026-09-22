# MCP tools

`babels_hoard/mcp_server.py` is a standalone stdio MCP server (imports only
the standard library, `httpx` and `mcp`). It forwards each tool call to
`POST /api/agent/<tool>` on the running app at `BABEL_URL` (default
`http://127.0.0.1:8811`). A non-loopback `BABEL_URL` is refused on every
call with `babel_misconfigured`; loopback traffic never goes through a
system proxy. Because the adapter is a thin forwarder, the same behaviour is
tested through FastAPI's `TestClient` (`tests/test_api.py`) and through the
real MCP protocol (`tests/test_mcp_protocol.py`).

Conventions:

- Results are JSON objects. Every item carries a stable `id` that can be
  passed back (`docs_read(id)`); entry ids look like
  `lib-<16 hex>:<qualname>`, library ids `lib-<16 hex>`, environment ids
  `env-<12 hex>`, job ids `job-<12 hex>`.
- Lists are capped and say so (`truncated`, `has_more`, `next_offset`).
  Signatures and summaries in search results are cut at 200 characters.
- `env` accepts an environment id, a registered project folder or
  interpreter path, or nothing (the most recently registered project; before
  any registration, Babel's own interpreter).
- A package is indexed the first time a lookup or check needs it, and again
  when its installed version changes; the answer is always about the version
  installed right now.
- Errors are tool errors whose text is `<code>: <message>`, e.g.
  `bad_path: not a directory: C:\docs` or `unknown_environment: ...`.
  Timeouts: `api_lookup`, `api_check_code`, `docs_add_environment`,
  `docs_index_folder` and `docs_catalog` wait up to 300 s (first-use
  indexing of a very large package), the others 30 s.
- Annotations: every tool has `destructiveHint=false`; `docs_catalog` and
  `docs_install_docset` have `openWorldHint=true` (they use the network);
  `docs_add_environment`, `docs_install_docset` and `docs_index_folder`
  have `readOnlyHint=false`.

## `docs_libraries(ecosystem?, env?, limit=30, offset=0)`

What is indexed (current versions only), the environments and the last five
background jobs. Use it to find an env id or follow a job.

```json
{"libraries": [{"id": "lib-599481efc933ad56", "ecosystem": "python", "name": "httpx",
   "version": "0.28.1", "env": "env-b50df97e0eff", "status": "done", "entries": 435}],
 "total": 6, "has_more": false, "next_offset": null,
 "note": "Packages installed but not listed here are indexed automatically on first api_lookup/api_check_code.",
 "environments": [{"id": "env-b50df97e0eff", "label": "Babel's own interpreter", "python": "3.11.15",
   "python_path": ".../.venv/bin/python", "node_modules_path": null, "builtin": true, "default": true}],
 "jobs": [{"id": "job-3f1c...", "kind": "install_docset", "status": "running", "progress": 0.45,
   "message": "converted 120/260 pages", "updated_at": "2026-09-22T21:10:04Z"}]}
```

`status` is `done` or `partial` (a size cap was hit; see ARCHITECTURE).

## `docs_search(query, library?, ecosystem?, kind?, env?, limit=8)`

Ranked full-text search (SQLite FTS5, bm25) over indexed APIs, docsets and
markdown sections. Identifiers are split on camelCase, snake_case and dots,
so `"send a get request"` matches `httpx.Client.get`. Re-exports of the same
definition count once. `limit` max 50. With no hits, `did_you_mean` offers
existing names close to the query.

```json
{"query": "send a get request",
 "results": [{"id": "lib-599481efc933ad56:httpx.Client.get", "qualname": "httpx.Client.get",
   "kind": "method", "library": "httpx@0.28.1", "ecosystem": "python", "env_id": "env-b50df97e0eff",
   "signature": "get(self, url: URL | str, *, params: QueryParamTypes | None = None, …",
   "summary": "Send a `GET` request.", "score": 18.2}],
 "count": 8, "truncated": true}
```

## `api_lookup(symbol, env?, library?)`

The installed truth about one dotted symbol (`pandas.DataFrame.merge`,
`httpx.Client`, `json.dumps`, `react.useState`). For classes, `params` are
the constructor's.

```json
{"found": true, "id": "lib-b02784968dd67bcb:pydantic.BaseModel.model_validate",
 "qualname": "pydantic.BaseModel.model_validate", "kind": "method",
 "signature": "model_validate(cls, obj: Any, *, strict: bool | None = None, ...) -> Self",
 "params": [{"name": "obj", "kind": "positional-or-keyword", "annotation": "Any", "required": true,
             "description": "The object to validate."},
            {"name": "strict", "kind": "keyword-only", "annotation": "bool | None", "default": "None"}],
 "returns": "Self - The validated model instance.", "summary": "Validate a pydantic model instance.",
 "doc": "... first 1500 characters ...", "doc_truncated": true,
 "next": "docs_read(id='lib-b027...:pydantic.BaseModel.model_validate', offset=1500) for the rest of the doc",
 "deprecated": false, "source": ".../pydantic/main.py:693", "library": "pydantic@2.13.5",
 "env": "env-b50df97e0eff"}
```

Modules and classes also return `members` (up to 25 public names) and
`members_total`; re-exported objects return `same_as` (the path where their
members are listed); a symbol also indexed in an older version returns
`other_versions_indexed`.

Not found:

```json
{"found": false, "symbol": "httpx.Client.gett", "certain": true, "closest_parent": "httpx.Client",
 "suggestions": ["httpx.Client.get"],
 "message": "'httpx.Client.gett' does not exist in httpx 0.28.1 installed in Babel's own interpreter.",
 "library": "httpx@0.28.1", "env": "env-b50df97e0eff"}
```

`certain: false` means the parent creates names dynamically or is only
partly indexed, so the symbol may exist at runtime. A package that is not
installed in the environment returns `certain: false` and a message saying
so.

## `api_check_code(code, env?, language="python")`

Parses `code` (never runs it) and checks every use of an installed library.

| code | severity | meaning |
| --- | --- | --- |
| `syntax_error` | error | the snippet does not parse |
| `unknown_module` | error/warning | an imported module does not exist in the installed package |
| `unknown_attribute` | error/warning | a name does not exist on the module, class or instance it is used on |
| `unexpected_keyword` | error | a keyword the function does not accept (and it has no `**kwargs`), or a positional-only parameter passed by keyword |
| `too_many_positional` | error | more positional arguments than the function accepts (no `*args`) |
| `missing_required` | error | a required argument is not supplied (only checked when the call has no `*`/`**` unpacking) |
| `deprecated` | warning | the target is marked deprecated |

`warning` for `unknown_*` means the namespace is fully known except for a
dynamic mechanism (`__getattr__`, `setattr(self, name, ...)`, a lazy module)
that could supply the name. Silent (`unchecked`) cases: values whose type
is unknown, names narrowed by `isinstance`/`issubclass`/`type` checks,
namespaces that are statically incomplete (star-imports from compiled
modules, `globals()`, helpers that write into the module while it is
imported, unresolved or builtin base classes, custom metaclasses, size
caps), private names, calls through unknown decorators,
constructors of classes with `__new__` or custom metaclasses, and code under
`try/except ImportError|AttributeError|TypeError|Exception`, `hasattr`,
`getattr`, version or platform checks.

Values are followed through `import`/`from ... import`, simple assignments,
annotated variables and parameters, function return annotations, methods
returning `Self`, `with`/`async with` (via `__enter__`/`__aenter__`), and
`await` (an un-awaited coroutine is not treated as its result).

```json
{"ok": false,
 "findings": [{"line": 6, "col": 12, "severity": "error", "code": "unknown_attribute",
   "symbol": "httpx.Response.jsonify",
   "message": "'jsonify' does not exist on an instance of 'httpx.Response' in httpx 0.28.1.",
   "suggestion": "json"},
  {"line": 7, "col": 4, "severity": "error", "code": "unexpected_keyword",
   "symbol": "client.post(retry=...)",
   "message": "'retry' is not a parameter of httpx.Client.post (httpx 0.28.1)."}],
 "truncated": false, "checked": 10, "unchecked": 0, "env": "env-b50df97e0eff",
 "libraries": ["httpx@0.28.1"]}
```

At most 25 findings are returned (`truncated: true` beyond that).
`language="typescript"` (v1) checks that names in `import { A, type B }
from "pkg"` / `export { A } from "pkg"` are exported by the installed
package (indexed on first use, needs Node.js); relative imports and packages
that are not installed are `unchecked`.

## `docs_read(id, offset=0, max_chars=4000)`

Reads the text of an entry (docstring, docset section or markdown section)
in chunks; `max_chars` max 20,000. Offsets index the same text `api_lookup`
returns in `doc`.

```json
{"found": true, "id": "lib-...:json.dumps", "qualname": "json.dumps", "library": "stdlib/json@3.11.15",
 "signature": "dumps(obj, *, skipkeys = False, ...)", "text": "Serialize ``obj`` to a JSON formatted ``str``...",
 "offset": 0, "total_chars": 2410, "has_more": false, "next_offset": null}
```

## `docs_add_environment(path, index_dependencies=true)`

Registers a project folder (detects `.venv`, `venv`, `env`, `.env` and
`node_modules`), an interpreter named like `python`/`python3.x`/`python.exe`,
or a `node_modules` folder. Registering again is harmless. With
`index_dependencies`, the direct dependencies from `pyproject.toml`
(`[project]`, optional dependencies, `[dependency-groups]`, Poetry) and
`requirements*.txt` are indexed in a background job, mapped to their import
names (`PyYAML` → `yaml`).

```json
{"environment": {"id": "env-9f32f7702138", "label": "C:\\code\\shop", "python": "3.13.1",
   "python_path": "C:\\code\\shop\\.venv\\Scripts\\python.exe", "node_modules_path": null, "builtin": false},
 "dependency_job_id": "job-5d0c1f2a9b7e", "dependencies": ["fastapi", "pandas", "pyyaml"],
 "message": "Registered. Indexing 3 direct dependencies in the background; api_lookup/api_check_code also index packages on first use."}
```

## `docs_catalog(query="", limit=10)`

Downloadable docsets from the public DevDocs catalogue (network). `limit`
max 50.

```json
{"query": "python", "results": [{"slug": "python~3.13", "name": "Python", "version": "3.13", "db_size_kb": 13213}],
 "count": 1, "total": 5, "truncated": true}
```

## `docs_install_docset(slug)`

Queues the download and indexing of one docset (network) and returns at
once; progress appears in `docs_libraries().jobs`.

```json
{"job_id": "job-3f1c0e9d2a44", "status": "queued", "slug": "python~3.13",
 "message": "Downloading and indexing in the background (can take a minute for large docsets). Call docs_libraries to see the job's progress; the docset is searchable when it shows status done."}
```

## `docs_index_folder(path, name?)`

Indexes `.md`/`.mdx`/`.rst`/`.txt` files by heading (headings inside fenced
code are ignored; rst underlined headings are recognised). Skips
`node_modules`, `.git`, virtual environments, `dist`/`build` and hidden
folders; at most 2,000 files and 2 MB per file (`status: partial` when the
file cap is reached).

```json
{"id": "lib-8c2e...", "name": "docs", "status": "done", "entry_count": 214,
 "note": "214 sections from 31 files",
 "hint": "Search it with docs_search(query, library=<name>); read a section with docs_read(id)."}
```

## Configuration for other MCP clients

```json
{
  "mcpServers": {
    "babels-hoard": {
      "command": "C:\\path\\to\\Babel's Hoard\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\Babel's Hoard\\babels_hoard\\mcp_server.py"],
      "env": { "BABEL_URL": "http://127.0.0.1:8811" }
    }
  }
}
```

On Linux/macOS use `.venv/bin/python`. The app must be running
(`babel_unavailable: Babel's Hoard is not running...` otherwise).
