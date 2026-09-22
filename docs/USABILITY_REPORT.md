# Usability report

First real use of Babel's Hoard, walking every scenario in
[USE_CASES.md](USE_CASES.md) twice: as a person in the browser (Playwright,
screenshots read one by one, English at 1280x800 and Spanish at 1920x1080)
and as an agent over the real MCP stdio protocol
([`scripts/agent_walkthrough.py`](../scripts/agent_walkthrough.py)). The
first pass recorded the findings below; the fixes and a second walk of every
scenario (same data, same scripts, a fresh data folder) are in
[Second pass](#second-pass-after-the-fixes) at the end.

## Test setup

- **Project:** `data-uxtest/faustus-like/` (gitignored), shaped like the
  project `C:\Projects\myapp`: `.venv` at the root with **228 installed
  distributions** (fastapi 0.141.1, httpx 0.28.1, pydantic 2.13.5,
  pydantic-settings 2.15.0, SQLAlchemy 2.0.54, fastembed 0.8.1, psutil
  7.2.2, numpy, pandas, chromadb, openai, mcp, alembic, pytest, mypy, ruff,
  celery, boto3, ...), 50 direct dependencies across `requirements.txt` and
  `pyproject.toml`, a React 19 `frontend/node_modules` (react, react-dom,
  lucide-react, zustand, @tanstack/react-query, typescript, vite) and a
  `docs/` folder of Markdown.
- **Reference module:** `backend/app/chat_service.py`, 370 lines in the
  usual FastAPI style (FastAPI router with `Annotated` dependencies, SQLAlchemy
  2.0 async ORM, pydantic v2 + pydantic-settings, httpx streaming client,
  tenacity `AsyncRetrying`, fastembed, psutil, PyJWT, PyYAML,
  sse-starlette). It is known-correct: its own test imports it and drives
  every endpoint through `TestClient` (1 passed) and `mypy` reports no
  issues.
- **Machine:** shared Linux container, 2 CPUs, 7 GB RAM, with other agents
  building sibling apps at the same time (load average 16-17 during most
  runs), so absolute timings are pessimistic. Relative timings (with and
  without the background job, cold and warm) are the useful part.

## Results at a glance

| Scenario | Person | Agent |
| --- | --- | --- |
| UC1 register the big project | Registered in 0.65 s, 228 packages listed in 0.3 s, dependency job with a moving progress bar | Registered in 0.63 s; a 50-dependency job starts automatically |
| UC2 look up `async_sessionmaker` | **Fails**: not in the index (see B2) | - |
| UC3 check a model-written snippet | 1 of 4 mistakes caught; then a false "No problems" after UC7 (B1) | - |
| UC4 write + prove an httpx endpoint | - | Works in 5 calls; one hallucinated method not caught (A4) |
| UC5 review the 370-line module | - | **0 findings on the correct module** (171 verified, 145 unverifiable); the 3 introduced mistakes all reported with the right lines. Outside this module, false errors on `from lxml import etree`, numpy's `Generator.normal` and chromadb namespace folders (B5, B6) |
| UC6 learn fastembed, write, check | - | Works; search ranks constants above `TextEmbedding.query_embed` (A9) |
| UC7 check React imports | Needs a second registration, which then breaks Python checks (B1) | - |
| UC8 search my own docs | **Not possible in the UI** (A6) | Works: 7 sections from 2 files, found and read |

No tool result contained an image block (22 calls). No console errors in
either browser run.

## Findings

Ranked by how much they hurt a real user. **Blocker** = a wrong answer or a
scenario that cannot be completed; **annoying** = works, but costs time,
trust or context; **cosmetic** = polish.

### Blockers

**B1 - A frontend-only environment becomes the default and turns every
Python check into a false "No problems".**
*UC7, UC3, UC4.* Registering the project root does not see
`frontend/node_modules` (only `<root>/node_modules` is detected), so the
developer registers `...\frontend` as well. That newer, Node-only environment
becomes the default: the Check screen preselects it, and
`api_check_code(code)` without `env` checks Python against it. Result for
code with two real errors (`httpx.Client(retries=3)`, `c.gett(...)`):
`{"ok": true, "findings": [], "checked": 0, "unchecked": 3}` - shown in the
UI as a green "No problems / Sin problemas". `api_lookup("httpx.Client.get")`
says httpx "is not installed". Registering the root again does not make it
the default back: the default is the most recently *created* environment,
and a re-registration does not update `created_at`.
*Fix planned:* detect `node_modules` one level down (`frontend/`, `web/`,
`client/`, `ui/`) and keep both in one environment; resolve the default per
language (newest environment that has a Python interpreter for Python
checks/lookups, newest with `node_modules` for TypeScript); when the chosen
environment has no interpreter, return an error (`no_python: ... pass
env=<id>`) instead of an all-unchecked "ok"; re-registering makes an
environment the default again. Regression tests for each.

**B2 - Large libraries are capped breadth-first, so a project's core APIs
are missing: `sqlalchemy.ext.asyncio` is not indexed at all.**
*UC2, UC5.* SQLAlchemy 2.0.54 hits the 15,000-entry cap
(`partial`). `sqlalchemy.ext.asyncio` is recorded but never expanded, so
`api_lookup` of `async_sessionmaker`, `AsyncSession`,
`AsyncSession.execute` and `create_async_engine` all answer
`found: false, certain: false` ("not in the static index"), and the search
"async sessionmaker" returns `sqlalchemy.orm.sessionmaker.kw` first. The
same cap hits numpy, pandas, openai and mypy. It also explains most of the
145 unverifiable uses in UC5.
*Fix planned (lazy indexing, as asked):* when a lookup or check reaches a
module that was not expanded because of the cap (`nx`/`trunc`), index that
submodule's subtree on demand and merge it into the library, so the cap
bounds the eager pass, not what can ever be answered. Test with a fixture
package larger than a lowered cap.

**B3 - `api_lookup("pytest.fixture")` says pytest is not installed when it
is (distributions with several import names).**
*UC5 extended to the project's tests.* pytest 9.1.1 ships `_pytest`, `py`
and `pytest`. The dependency job indexes each import name of a distribution
into the **same library id**; the first one (`py`) is written, and the
second is skipped because the library already exists. The library "pytest
9.1.1" therefore holds 5 entries of `py`, `api_lookup("pytest.fixture")`
answers "'pytest' is not installed", and a test file with
`@pytest.fixtur` and `pytest.raises(..., matchh=...)` checks as `ok: true,
checked: 0`. The same happens to any multi-package distribution (setuptools
/ `pkg_resources`, attrs / `attr`, protobuf, pywin32) and in the UI's
per-package "Index" button, which picks `import_names[0]`. Lookups made
before the job ran are not affected, so the answer depends on the order of
events.
*Fix planned:* one library row per (distribution, version, import name),
or all import names of a distribution loaded into one library in a single
pass; test with a fixture distribution that ships two top-level packages.

**B4 - The first line of every tool description is a wrapped sentence
fragment with no trigger words (a live issue observed in testing).**
*All agent scenarios.* Faustus's prompt listing keeps only the first 120
characters of line one of each description, and until a Faustus patch
lands its tool index only sees that. Today line one is 73-84 characters of
a sentence cut by docstring wrapping ("Exact signature, parameters (with
defaults and which are required), return type,") and the English/Spanish
keywords sit in the last lines, which the listing never shows. A Spanish
request ("comprueba este código") has nothing to match.
*Fix planned:* rewrite line one of every tool (at most 110 characters) as a
complete statement of what it does plus 2-4 English and Spanish trigger
words, keep the rest of each docstring, and add a test that checks length
and the presence of a Spanish word. Proposed lines (98-105 characters):

| Tool | New first line |
| --- | --- |
| `docs_libraries` | List indexed libraries, exact versions and project environments (libraries, librerías, versión, entornos) |
| `docs_search` | Search installed APIs and docs by words (search docs, buscar función, cómo se hace, documentación) |
| `api_lookup` | Exact signature of an installed API (signature, firma, parámetros, documentación, versión instalada) |
| `api_check_code` | Check code for hallucinated or misused APIs of installed versions (check code, comprobar código, validar) |
| `docs_read` | Read the full text of one doc entry by id, in chunks (read docs, leer documentación, seguir leyendo) |
| `docs_add_environment` | Register a project folder or venv so its packages can be checked (register project, registrar proyecto) |
| `docs_catalog` | List downloadable offline docsets; uses internet (docset catalogue, catálogo, documentación offline) |
| `docs_install_docset` | Download and index one offline docset in background (install docs, instalar documentación, descargar) |
| `docs_index_folder` | Index a folder of Markdown docs for search (index folder, indexar carpeta, documentación del proyecto) |

**B5 - False errors on correct code: compiled submodules and namespace
sub-packages are treated as missing.**
*UC5 extended (false-positive harness over a large real dependency stack, plus
hand-written probes).* The README promises an error only when absence is
proven, but these correct lines are reported as **errors**:

| Correct code | Reported |
| --- | --- |
| `from lxml import etree` / `import lxml.etree as ET` | `'etree' cannot be imported from 'lxml' in lxml 6.1.3` |
| `from sqlalchemy.cyextension import util` | `'util' cannot be imported from 'sqlalchemy.cyextension'` |
| `from chromadb.api.models.Collection import Collection` (the import chromadb's own `api/client.py` uses) | `'models' is not a submodule or member of 'chromadb.api'` |

`lxml/etree` is a compiled extension (`etree.cpython-311-...so`, no stub)
inside a pure-Python package; `chromadb/api/models/` is a folder without
`__init__.py` (an implicit namespace sub-package). Neither becomes a member
of its parent, and the parent's member list is still marked complete.
`scripts/check_corpus.py` over sqlalchemy, pydantic-settings, sse-starlette,
tenacity, fastembed, psutil, chromadb, aiosqlite, alembic and PyJWT
(80 files, 2,092 verified uses, 230 s) reported 13 errors: 7 false (6
namespace folders in chromadb, 1 compiled submodule in SQLAlchemy) and 6
imports of chromadb `*_pb2` modules that are genuinely absent from the
wheel (distributed-mode code, not false positives); plus 2 warnings on
pypika's dynamic `Table` attributes, which are correct warnings.
*Fix planned:* record compiled submodules (`*.so`/`*.pyd`) and
sub-directories with Python files as members of their package (compiled
ones as `compiled`, i.e. unverifiable members); tests in `edgelib` for both
layouts.

**B6 - False errors on numpy: methods declared only with `@overload` in a
stub are dropped.**
`np.random.default_rng(0).normal(size=3)` reports `'normal' does not exist
on an instance of 'numpy.random.Generator' in numpy 2.4.6`. The class comes
from `numpy/random/_generator.pyi`, where `normal`, `integers`, `random`,
`choice`, `uniform` and most other methods are declared only as `@overload`
signatures (a stub has no implementation). The index keeps 12 members of
`Generator` - exactly the non-overloaded ones - and marks the class
complete. Every stub-typed package that overloads (numpy above all) is
exposed. *Fix planned:* when a stub name has only overloads, index the name
with its overload contracts; test with an overload-only stub in `edgelib`.

### Annoying

**A1 - Registering a big project starts a long eager job that slows the
very first check and holds a lot of memory.** *UC1, UC4, UC5.*
Registration itself is fast (0.4-0.65 s) and the UI shows moving progress
("indexed alembic (3/50)"). But `docs_add_environment` then indexes all 50
direct dependencies serially, including tools that are never imported by
application code (mypy alone is a capped 15,000 entries; ruff, pytest-asyncio),
in 4 min 15 s - 4 min 36 s. The job shares the process (and the GIL) with
requests: the first `api_check_code` on the 370-line module took **129 s
while the job ran versus 78 s alone** (0.09 s once indexed). The process
reached 1.5-1.6 GB RSS after the job and never returned it (56 MB after a
restart with the same database); on this 7 GB box one instance was killed
by the out-of-memory killer. On a developer's machine this competes with a loaded
27B model.
*Planned:* skip dev-only groups and tool distributions in the eager job,
let on-demand indexing jump ahead of the job, and run indexing in a child
process so memory is returned (or at least `gc` + arena trim after each
library); keep the job's progress visible.

**A2 - `async with client.stream(...) as response` loses the type, so a
hallucinated `response.aiter_text_lines()` is not caught.** *UC4.*
`httpx.AsyncClient.stream` is an `@asynccontextmanager` method annotated
`-> AsyncIterator[Response]`; the checker does not map it to `Response` in
`async with`, so a commonly used streaming idiom is unverified.
*Planned:* treat `contextlib.(async)contextmanager` functions returning
`(Async)Iterator[T]` / `(Async)Generator[T, ...]` as yielding `T`.

**A3 - pydantic v1 idioms on the user's own models are not flagged.** *UC3.*
In the UC3 snippet only `httpx.AsyncClient(retries=3)` was reported (1 of
4 mistakes). `item.dict()` on `class Item(BaseModel)` is deprecated in
pydantic 2 but the checker does not follow classes defined in the snippet;
`class Config: orm_mode = True` is out of reach of a static name check.
*Planned (small):* instances of classes defined in the snippet whose bases
are fully known inherit the bases' members for attribute and deprecation
checks (only names the class body does not define).

**A4 - `psutil.gpu_percent()` is not caught.** *UC3.* psutil fills
`RLIM*` constants through `globals()` in a loop guarded by
`_name.startswith('RLIM') and _name.isupper()`, which marks the whole module
dynamic, so every missing name is only "may exist at runtime".
*Planned:* when every dynamic write is guarded by a literal prefix, record
the prefix and keep absence certain for other names.

**A5 - Libraries screen does not refresh during the dependency job.** *UC1.*
"Indexed here" keeps saying "Nothing indexed yet" and every package row
keeps "NOT INDEXED" until the whole 4-minute job ends, although the bar
says 19/50. *Planned:* reload the list on each progress change.

**A6 - No way to index a Markdown folder from the UI.** *UC8.* The endpoint
exists (`POST /api/folders/index`) and the agent tool works, but no screen
offers it; the Docsets page only has the DevDocs catalogue. *Planned:* an
"Index a folder" card on Docsets with the result note.

**A7 - `unknown_environment` does not say what to do.** *UC4 (agent).*
`api_lookup(..., env="<a real project path not registered yet>")` returns
`unknown_environment: unknown environment: '<path>'`. For a small model the
next step should be in the message ("register it with
docs_add_environment(path) or pass an id from docs_libraries"), or a real
project folder could simply be registered on first use.

**A8 - Large default results for a small context.** *UC4 (agent).*
`docs_libraries()` with 50 indexed libraries returns 7,855 characters (~2k
tokens), mostly per-library notes ("bases/re-exports resolved from
__future__, typing, ..."). `api_lookup("httpx.AsyncClient.stream")` is
3,168 characters. Everything else stayed under 1k tokens (the warm run:
21 calls, 29,289 characters in total). *Planned:* drop the resolution note from the agent
listing (keep it in the UI) and default `limit` to 15.

**A9 - Search by meaning ranks constants first.** *UC6.*
`docs_search("query embedding", library="fastembed")` returns
`Task.RETRIEVAL_QUERY`, `JinaEmbeddingV3.QUERY_TASK` and
`ColPali.QUERY_PREFIX` before `TextEmbedding.query_embed`. A boost for
functions/methods and for the package's public top-level classes would put
the useful hit first.

**A10 - Capped JavaScript packages make icon imports unverifiable.** *UC7.*
lucide-react exports more than 500 names, so it is `partial` and
`import { MagicWand } from "lucide-react"` (no such icon) is "unverifiable".
Named-import checks only need the export *names*, which are cheap; the cap
could apply to members, not to top-level names.

**A11 - Weak suggestions.** `httpx.Timeout(connect_timeout=5)` suggests
`timeout` (the right one is `connect`); `useFormStatus` from "react"
suggests `useState` (it lives in `react-dom`). Prefer parameter names that
share the longest prefix/suffix, and for a JS name that another installed
package exports, say which.

### Cosmetic

- **C1** "1 errors" / "1 errores": pluralisation of the summary pill.
- **C2** Spanish UI shows English backend strings: kind badges
  (ATTRIBUTE, METHOD), library notes ("indexed 15000 entries (capped, ...)"),
  finding messages; `<html lang>` stays `en` in Spanish.
- **C3** The sidebar tagline wraps to two lines at both widths.
- **C4** Assistant activity shows "14733 chars, python" without the
  environment the call used, which is what you want to know after B1.

## What worked well

- Zero false positives on the correct 370-line module, on the 23-line
  fastembed module and on the project's test file (the latter only because
  pytest went unchecked, B3); the three introduced
  mistakes (a wrong import name, `connect_timeout=`, `tag=`) were all
  reported as errors with exact lines and useful messages naming the
  installed version.
- Error results for bad ids, bad paths, bad slugs and syntax errors are
  short and, except A7, say what to do next.
- Ids and paths chain cleanly between calls (env id from
  `docs_add_environment` into every later call, entry id from
  `docs_search` into `docs_read`).
- Nothing blocks the UI while indexing runs: search answered in about a
  second during the dependency job.
- Both languages are complete in the interface's own strings; no console
  errors; layout holds at 1280x800 and 1920x1080.

## Not tested in this pass

- The Windows launchers and real Windows paths (`D:\...`, `Scripts\python.exe`)
  - this was Linux.
- "Ask the docs" with a real model (none available here; the disabled state
  and its reason were checked).
- Docset download/install (network) and dark mode screenshots.
- Faustus itself: the listing truncation was simulated by the walkthrough
  script (first line, 120 characters), not observed in Faustus.

## Second pass (after the fixes)

Same project and scripts, fresh data folder, in English at 1280x800 and in
Spanish at 1920x1080 (screenshots read one by one), and the agent script
over MCP stdio. Every regression is pinned by a test in
[`tests/test_usability_fixes.py`](../tests/test_usability_fixes.py).

| Scenario | Person | Agent | Verdict |
| --- | --- | --- | --- |
| UC1 register the big project | 0.35-0.46 s; the root finds `frontend/node_modules` and is tagged *default · Python* and *default · TypeScript*; 228 packages in 0.3 s; the 46-dependency job (dev tools listed as skipped) fills "Indexed here" while it runs; search answers in 0.1-0.2 s meanwhile | - | works |
| UC2 look up `async_sessionmaker` | Typing the dotted name offers *Look up*; the panel shows the constructor of SQLAlchemy 2.0.54 (34-40 s the first time: SQLAlchemy indexed plus the capped namespace expanded while the dependency job was running; instant afterwards); "async sessionmaker" then ranks the class first | - | works (slow first time) |
| UC3 check a model-written snippet | 2 of 4 caught: `retries=3` (error) and `item.dict()` (deprecated, with the `model_dump` hint); `psutil.gpu_percent()` stays unverifiable (A4), `class Config: orm_mode` is out of reach | - | works with caveat |
| UC4 write + prove an httpx endpoint | - | 5 calls; the hallucinated `aiter_text_lines` is now caught through `client.stream(...) as response`; both errors fixed from the suggestions; final check clean | works |
| UC5 review the 370-line module | - | 0 findings on the correct module (177 verified, 143 unverifiable); the 3 introduced mistakes reported with lines, `connect_timeout` now suggests `connect`; lxml / numpy `Generator.normal` / chromadb namespace imports clean; `@pytest.fixtur` and `pytest.raises(..., matchh=)` both reported | works |
| UC6 learn fastembed, write, check | - | `docs_search("query embedding")` puts `fastembed.TextEmbedding.query_embed` first; the written file checks clean | works |
| UC7 check React imports | The root registration alone is enough; `useFormStatus` and `MagicWand` flagged; registering `frontend` separately keeps Python on the root; choosing the frontend-only environment for Python shows why and disables Check | Python check against it: `no_python` naming the right ids; without env the Python default is unchanged | works |
| UC8 search my own docs | Docsets -> *Index a folder*: 7 sections from 2 files; "how do I start the backend" finds `SETUP.md#Start the backend` and opens it | Same, via `docs_index_folder` + `docs_search` + `docs_read` | works |

Agent totals: 28 calls, 30,616 characters of results (~7.7k tokens), largest
`docs_libraries` at 4,667 characters (was 7,855); no image blocks; the
walkthrough reported no problems. No console errors in either browser run.
The app process stayed at 200-250 MB during and after the dependency job
(1.4-1.6 GB before the fix).

### Status of each finding

| Finding | Status |
| --- | --- |
| B1 frontend-only env becomes the default | **Fixed.** Frontend `node_modules` detected one level down; defaults per language; re-registering makes the project the default again and keeps its id; a Python check against a `node_modules`-only env is a `no_python` error listing the Python envs; the Check screen picks the env per language, explains a mismatch and disables Check |
| B2 capped namespaces missing (`sqlalchemy.ext.asyncio`) | **Fixed.** Namespaces the cap left unexpanded are indexed on the first lookup or check that reaches them and merged into the same library |
| B3 pytest "not installed" | **Fixed.** One library per import name of a distribution; siblings are not superseded; the UI's *Index* button indexes every import name and the Libraries list says which is which. Also fixed on the way: stubs that re-export another package (`attrs` -> `attr`) failed to index at all |
| B4 first description line | **Fixed** as proposed; tested (at most 110 characters, a whole statement, Spanish trigger words) |
| B5 compiled submodules / namespace packages | **Fixed**; recorded as unverifiable modules |
| B6 overload-only stub methods | **Fixed**; indexed with their overload contract |
| A1 eager job cost and memory | **Fixed**: dev groups/files and tools skipped (still indexed on demand); the memory growth was a leak (griffe's dataclass cache and our docstring cache kept every parsed package alive), now cleared after each library. Not done: letting on-demand indexing jump ahead of the job (it waits for at most the library being indexed) |
| A2 `async with client.stream(...)` | **Fixed** (`@(async)contextmanager` yields) |
| A3 pydantic v1 idioms | **Partly fixed**: snippet classes follow their bases and PEP 702 `@deprecated` is read, so `item.dict()` is reported; `class Config: orm_mode` is not |
| A4 `psutil.gpu_percent()` | **Not fixed.** psutil also extends `__all__` from a platform module chosen at import time; proving absence there needs more than a prefix rule, so it stays unverifiable rather than risk false errors |
| A5 Libraries not refreshing during the job | **Fixed** |
| A6 no UI to index a Markdown folder | **Fixed** (*Index a folder* on Docsets) |
| A7 `unknown_environment` without next step | **Fixed** (says to register with `docs_add_environment` and lists known ids) |
| A8 large default results | **Fixed**: `docs_libraries` pages 15 by default (the per-library notes were already not in the agent listing) |
| A9 search ranks constants first | **Fixed** (kind, name match, depth and legacy-path re-ranking; exact identifiers always considered); also fixed: an older, slower search answer could replace the current one in the UI |
| A10 capped JS export lists | **Fixed** (names past 500 exports indexed without types) |
| A11 weak suggestions | **Fixed** for compound keywords (`connect_timeout` -> `connect`) and for a JS name exported by another *indexed* package; `useFormStatus` still suggests `useState` until `react-dom` has been indexed |
| C1 "1 errors" | **Fixed** |
| C2 English strings in the Spanish UI | **Partly fixed**: kind badges and filters translated, `<html lang>` follows the UI; finding messages, job progress and library notes come from the backend in English |
| C3 tagline wraps | **Fixed** (shorter tagline, one line at both widths) |
| C4 activity without the environment | **Fixed** (each call's summary names the environment that answered) |

New in the second pass, found by the false-positive harness on `_pytest`:
`logging.LogRecord.message` (assigned by `Formatter.format` on the record,
declared by no class) was an error; attributes a module assigns on other
objects are now recorded on its classes. The harness over 110 files of the
large dependency stack (SQLAlchemy, pydantic-settings, sse-starlette, tenacity,
fastembed, psutil, chromadb, aiosqlite, alembic, PyJWT, pytest, attrs,
numpy) now reports 3 errors, all genuine (`chromadb.proto.*_pb2` modules its
wheel does not ship).

Still not tested: the Windows launchers and paths, "Ask the docs" with a
real model, docset downloads, dark mode, and Faustus's own tool listing
(simulated by the walkthrough).

