# Architecture

## Modules

```
babels_hoard/
  api.py            FastAPI app: browser guard, /api/agent/*, UI endpoints, static hosting
  mcp_server.py      standalone stdio MCP adapter (stdlib + httpx + mcp only)
  db.py              sqlite3 connection + schema (WAL, FTS5)
  environments.py    detect/register/probe interpreters and node_modules folders
  indexing.py        griffe-based static Python indexing
  node_indexing.py   shells out to probes/ts_probe.mjs (TypeScript compiler API)
  checker.py         api_check_code: ast-based hallucination/misuse checks
  search.py          FTS5 search, api_lookup, docs_read
  docsets.py         DevDocs catalogue/install, HTML->Markdown section split
  html_to_markdown.py  small stdlib-only HTML->Markdown converter
  markdown_index.py  folder-of-markdown indexing
  deps.py            read pyproject.toml/requirements*.txt for direct deps
  jobs.py            background job queue (one worker thread) + jobs table
  probes/
    python_probe.py  stdlib-only subprocess probe (never imports user code)
    ts_probe.mjs      TypeScript compiler API probe (reads only .d.ts files)
```

No module except `api.py` and `mcp_server.py`'s own HTTP calls talks HTTP;
the core (`indexing.py`, `checker.py`, `search.py`, ...) only takes a sqlite
connection and plain dicts, which is what makes it fast and offline to
test (see `tests/`).

## Data model (SQLite, WAL)

- `environments`: one row per registered interpreter/node_modules pair.
  `id` is a hash of `(python_path, node_modules_path)`, so re-registering
  the same paths is idempotent.
- `libraries`: `(ecosystem, name, version, source)` - one row per indexed
  package/docset/markdown-folder-version. `status` tracks
  `pending|indexing|done|partial|error` so partial coverage (a package that
  hit the entry cap) is visible rather than silently incomplete.
- `entries`: one row per indexed symbol/section. `qualname` is the *public*
  dotted path (what you'd actually type to reach it), not griffe's internal
  definition path - see "Public path vs. definition path" below.
  `entries_fts` (FTS5, `content=entries`) is kept in sync by SQL triggers.
  A trigram table exists for future "did you mean" work beyond the
  difflib-based one currently used.
- `docsets`, `jobs`, `agent_calls`, `settings`: as named.

## Python indexing (griffe)

1. `python_probe.py` runs as a subprocess *of the target interpreter*
   (never the app's own interpreter) and reports `sys.path`, the stdlib
   path, and every installed distribution's name/version/top-level import
   names, using only `importlib.metadata` - it never imports the target
   project's own packages.
2. `find_distribution()` maps an import name to a distribution. Two passes:
   an exact `distribution.name == import_name` match always wins before
   falling back to a `top_level.txt` scan, because some distributions (e.g.
   `griffe` 2.x, which is split into `griffe`/`griffelib`/`griffecli`) share
   the same top-level import name across several installable packages.
3. `griffe.load(name, search_paths=..., allow_inspection=False,
   resolve_aliases=True, submodules=True)` parses source with the AST -
   nothing from the target project executes.
4. `_walk()` recurses the public member tree (skipping `_private` unless
   listed in `__all__`), capped at `MAX_ENTRIES_PER_LIBRARY` (1500) and
   `MAX_DEPTH` (8) so a huge package (`torch`) cannot block the indexing
   thread for minutes; hitting the cap marks the library `partial` rather
   than silently truncating without saying so.
5. **Public path vs. definition path.** Cycle detection uses a per-branch
   `ancestors` set (not a single global "visited" set), so the same
   definition reachable from two different public parents (say, a method
   inherited by two sibling classes) gets its own entry under each public
   path, while a genuine alias cycle still terminates.
6. **Unresolvable aliases are still recorded.** A conditional cross-module
   alias griffe cannot statically resolve (the canonical example is
   `os.path`, which CPython assigns to `posixpath` or `ntpath` inside an
   `if`/`elif` at module scope) is written as a bare marker entry
   ("Babel could not statically resolve this name...") instead of being
   skipped. If it were skipped, `api_check_code` would see no entry for
   `os.path` and wrongly report it as missing - a false positive. With the
   marker entry, the name is known to exist, and any deeper attribute
   access under it correctly falls back to `unchecked` (see
   `tests/test_checker.py::test_unresolvable_alias_does_not_produce_false_positive`).
7. Inherited members: a class's entries include `griffe`'s
   `inherited_members` for names not overridden locally, so
   `api_check_code` can validate a call to an inherited method.
8. Docstrings are parsed by trying google/numpy/sphinx in turn and keeping
   whichever finds the most per-parameter descriptions; the *signature*
   itself (names, kinds, annotations, defaults) always comes from griffe's
   real `Parameters`, never from the docstring, since a docstring can lie
   about arity.

## JS/TS indexing

`node_indexing.py` shells out to `probes/ts_probe.mjs` (TypeScript compiler
API): it resolves the package's `.d.ts` entry point (`types`/`typings`
field, `exports["."].types`, `index.d.ts`, or an `@types/<name>` package),
builds a `ts.Program` from that file alone, and reads
`checker.getExportsOfModule()` plus one level of class/interface members.
Nothing but declaration files is read; no `.js` executes. If Node.js is not
on PATH, `NodeIndexError("Node.js not found")` propagates as a clear 502
from the API rather than a crash.

## Checker (`checker.py`)

An `ast.NodeVisitor` with a **per-scope binding dict**, copied (not shared)
on entering a function/class body so a reassignment inside one function
cannot leak into sibling code and cause a false positive elsewhere. It
tracks: import bindings (`import a.b as c`, `from a import b as c`,
relative imports ignored per spec), and simple `x = mod.Class(...)` /
`x = mod.func(...)` (when the function's return annotation names an
indexed class) assignments so a chained call like `x.method(...)` can be
checked too. Every branch it cannot resolve with confidence increments
`unchecked` rather than guessing - see the module docstring in
`checker.py` and `docs/MCP.md`'s `api_check_code` section for the exact
rule list.

## HTTP layer

`BrowserGuardMiddleware` rejects any request whose `Host` header is not
`127.0.0.1:<port>`/`localhost:<port>` (DNS rebinding), and rejects any
non-GET/HEAD/OPTIONS request that carries a foreign `Origin` or
`Sec-Fetch-Site: cross-site` (a malicious page's simple form POST). No CORS
headers are ever sent, so a foreign page cannot read a response even for
the GETs it is allowed to send. Every `/api/agent/*` call is timed and
logged to `agent_calls` (tool, a short args summary, duration, ok/error),
which the "Assistant activity" UI page reads back - the audit trail the
contract asks for.

## Jobs

A single daemon worker thread drains a `queue.Queue`; each job's progress
is written to the `jobs` table so the UI can poll `/api/jobs/{id}` instead
of holding an HTTP request open for a multi-minute docset install or a
project-wide dependency index. `docs_add_environment`'s
`index_dependencies=true` path returns immediately with a `dependency_job_id`
for the same reason - indexing a project's full dependency list is not
bounded work the MCP tool call should block on.

## Threading and SQLite

`sqlite3.connect(..., check_same_thread=False)` plus a single process-wide
`threading.RLock()` around every read/write in `api.py` and the job worker.
This is a deliberate simplicity choice for a single-user local tool: the
lock serializes what would otherwise be concurrent writers (the FastAPI
thread pool + the job thread), trading a small amount of throughput for
never having to reason about SQLite's actual concurrent-write semantics.
