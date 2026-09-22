# Architecture

## Modules

```
babels_hoard/
  __main__.py        python -m babels_hoard: port check, demo seeding, uvicorn on 127.0.0.1
  api.py             FastAPI app: browser guard, /api/agent/* (audited), UI endpoints, SPA hosting
  mcp_server.py      standalone stdio MCP adapter (stdlib + httpx + mcp only)
  db.py              schema (WAL, FTS5, trigram), in-place migrations, one connection per thread
  jobs.py            background job queue: one worker thread, progress in the jobs table
  environments.py    detect/register environments, run and cache the interpreter probe
  indexing.py        griffe-based static Python indexing with completeness metadata
  symbols.py         environment-scoped symbol resolution shared by lookup and checker
  checker.py         api_check_code for Python (ast) and TypeScript imports (v1)
  search.py          FTS5 search, api_lookup payloads, docs_read, library listings
  node_indexing.py   runs probes/ts_probe.mjs (TypeScript compiler API) on node_modules
  docsets.py         DevDocs catalogue and install, split by section anchors
  html_to_markdown.py  small stdlib-only HTML -> Markdown converter
  markdown_index.py  folder-of-markdown indexing
  deps.py            direct dependencies from pyproject.toml / requirements*.txt
  probes/
    python_probe.py  stdlib-only script run by the *target* interpreter
    ts_probe.mjs     TypeScript compiler API probe (reads only declaration files)
```

The core modules take a `sqlite3.Connection` and plain dicts and never
import FastAPI, so they are tested directly and offline.

## Data model (SQLite, WAL)

- `environments`: registered interpreter and/or `node_modules`. The id is a
  hash of the two paths, so registering again is idempotent. The default
  environment is the most recently registered non-builtin one.
- `libraries`: `(ecosystem, name, version, source)`. `status` is
  `indexing | done | partial | error | superseded`. `partial` means a size
  cap was hit (the note says which); `superseded` is an older installed
  version kept only for "other versions" and never used for answers.
- `entries`: one row per symbol or section. `qualname` is the public dotted
  path. Python rows also have `target` (griffe's canonical definition path),
  `parent_id` and `meta_json` (below). `entries_fts` (FTS5, external
  content) and `names_trigram` (FTS5 trigram, for "did you mean") are kept
  in sync by triggers.
- `docsets`, `jobs`, `agent_calls`, `settings`, `meta` (schema version).
  `db._migrate` upgrades older databases in place; a v1 database loses its
  Python/JS indexes (they lack the completeness metadata) and rebuilds them
  lazily.

## Threads and SQLite

`db.Database` gives every thread its own connection to the same WAL file
(`busy_timeout` 30 s). Request threads, the job worker and health checks
never share a connection, readers never wait for writers, and
`/api/health` answers while a long tool call runs (tested). Rebuilding one
library is a single short transaction - rows are computed first, then
`DELETE` + `INSERT` + status update are written together - under
`indexing.INDEX_LOCK`, so two threads cannot interleave rebuilds.

## Python indexing

1. **Probe.** `python_probe.py` runs as a subprocess of the target
   interpreter (only files named like `python`, `python3.x`, `python.exe`
   are ever run; `CREATE_NO_WINDOW` on Windows). It reports `sys.path`
   without its own folder, the stdlib path, builtin module names and every
   distribution with its import names (`top_level.txt`, else `RECORD`).
   It imports nothing from the environment. Results are cached per
   interpreter and invalidated when the interpreter or a site-packages
   folder changes (installs add or remove `*.dist-info` folders).
2. **Which distribution.** `find_distribution` maps an import name to the
   distribution that ships it (`yaml` → `PyYAML`); a distribution that
   ships no modules (griffe 2.x's `griffe` wraps `griffelib`) is used only
   if nothing else provides the name. Dependencies read from project files
   go the other way (`index_python_dependency`).
3. **Loading.** A `griffe.GriffeLoader` subclass parses source and `.pyi`
   stubs (`allow_inspection=False`: nothing executes), skips `tests`/`test`
   folders, and stops after 1,500 modules of the package. Other packages are
   loaded into the same collection only when needed to resolve a base class
   or a re-export (at most 16 packages, 600 modules); imported helpers that a
   module does not re-export (`from warnings import warn` under an
   `__all__` that omits it) are recorded without loading their package.
4. **Walk.** Breadth-first from the package root, so the public surface is
   indexed before internals. Each module or class is expanded once, at its
   *home* (the first public path that reaches it); other paths to the same
   object get an entry with `meta.see = <home>`. Classes include inherited
   members and public `self.x = ...` assignments made outside `__init__`.
   `__init__`, `__call__`, `__enter__` and `__aenter__` are kept (for
   constructor, call and context-manager checks); other private names only
   when listed in `__all__`. Cap: 15,000 entries; namespaces not expanded
   because of the cap are flagged, the library becomes `partial`.
5. **Completeness facts** (`meta_json`, see the `indexing.py` docstring):
   - modules: `dyn=1` for star-imports from compiled or unloaded modules,
     `globals()`/`vars()`/`sys.modules[...]` writes, `@enum.global_enum`
     (how `re` exports its flags), and module-level calls that write names
     into a module while it is imported - decided by reading the callee's
     source with a small AST data-flow check (writes through `globals()`,
     `f_globals`, `__dict__` or `sys.modules[...]` aliases; reads do not
     count), or, when the callee cannot be read, by it being handed
     `__name__` (enum's `_convert_` in `ssl`); `dyn="soft"` for a module
     `__getattr__`. Names listed in `__all__` but not defined statically
     are recorded as existing (lazy exports such as
     `concurrent.futures.ThreadPoolExecutor`);
   - classes: `dyn=1` for unresolved or builtin bases, metaclasses other
     than ABCMeta/Enum/Protocol/pydantic's, unknown class decorators, and
     classes defined in a module that star-imports code Babel cannot read
     (the pure-Python `datetime.timezone` is replaced by `_datetime`'s);
     attributes attached after the class body (`timezone.utc = ...`,
     `setattr(Color, "GREEN", ...)`) are added as members;
     `dyn="soft"` for `__getattr__` or `setattr(self, <computed name>)`
     outside pickling methods; `ctor` when calling the class runs the
     indexed `__init__` (no `__new__`, compatible metaclass);
   - functions: method kind (instance/class/static), `sig0` when a
     decorator may change the signature (known transparent ones such as
     `functools.wraps`, `lru_cache`, `property`, docstring appenders are
     trusted; `deprecate_kwarg`-style renamers are not), overload contracts
     (`ovn`/`ovk`), async, and the returned class (`ret`, `rself`).
   - `nx`/`trunc`/`unres`/`compiled`: not expanded, cut by the cap, an alias
     griffe could not resolve (`os.path`), a compiled module without stubs.
6. **Freshness.** `current_library` computes the library id for the
   version installed now; if it is not indexed it indexes it and marks
   older versions of the same distribution `superseded`. Imports that are
   not installed are remembered (per probe fingerprint) so they do not
   re-trigger work, and never create rows.

## Symbol resolution (`symbols.py`)

`SymbolIndex(conn, env)` resolves `a.b.c` member by member, following
`see` to the home that holds the members. A missing member comes back as
`Missing(parent, name, certainty)`: `error` when the parent's member list is
statically complete, `warning` for soft-dynamic parents, `unknown`
otherwise (private names, attributes/functions as parents, `dyn=1`, caps,
unresolved aliases, compiled modules). `api_lookup` and `api_check_code`
both use it, so they never disagree.

## Checker (`checker.py`)

An `ast.NodeVisitor` evaluates expressions to `Value`s (module, class,
instance, callable with its receiver) using imports, assignments,
annotations, return annotations, `Self`, `__enter__`/`__aenter__` and
`await`. Any store to a name forgets it (assignments of unknown values,
parameters, loop and comprehension targets, `except ... as`, `match`
captures, `global`), and so does an `isinstance`/`issubclass`/`type` check
on it (narrowing to a subclass); function and class bodies get a copy of the
enclosing bindings; comprehension generators are visited before their
element. Attribute stores/deletes are not checked (they create attributes).
Code under `try` with import/attribute/type/general exception handlers, and
under `if`/boolean/ternary tests that mention `hasattr`, `getattr`,
version, `TYPE_CHECKING`, `sys.platform` or `os.name`, is guarded: findings
there are counted as unchecked. Argument checks drop the first parameter
only for bound methods and constructors, trust overloads for keywords only,
and skip unknown decorators.

`scripts/check_corpus.py` runs the checker over the source of installed
packages as a false-positive harness (any error there is suspect).

TypeScript (v1): named imports and re-exports are matched against the
top-level exports of the installed package's declarations (indexed on
first use); capped export lists and packages that are not installed are
unchecked.

## HTTP layer

`BrowserGuardMiddleware`: a `Host` other than `127.0.0.1:<port>` /
`localhost:<port>` is rejected (DNS rebinding); writes with a foreign
`Origin` or `Sec-Fetch-Site: cross-site` are rejected; cross-site GETs to
`/api/*` other than `/api/health` are rejected unless they are top-level
navigations (some reads index packages). No CORS headers. The SPA route
serves a file only if its resolved path stays inside `frontend/dist`.
Validation and routing errors use the same `{error, message}` shape as tool
errors. `/api/agent/*` calls are timed and written to `agent_calls`; the UI
uses separate endpoints (`/api/lookup`, `/api/check`, ...) so the audit log
only shows the assistant.

## Jobs

One daemon worker thread drains a queue; each job function receives the
worker's own connection and a `progress(pct, message)` callback that
updates the `jobs` row. Dependency indexing and docset installs run there;
the MCP tools return a job id immediately and `docs_libraries` reports the
five most recent jobs.

## Frontend

React 19 + Vite + TypeScript strict. Hash routes (`#/search`,
`#/libraries`, ...), strings in `i18n.ts` (English, Spanish), light/dark
from `prefers-color-scheme` or the header toggle. `Signature.tsx` colours
signatures with a small tokenizer; `Markdown.tsx` renders docstrings
(Markdown, numpydoc/rst headings, admonitions, doctest blocks) by building
React elements - no index content is injected as HTML.
