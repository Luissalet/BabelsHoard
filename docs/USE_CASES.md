# Use cases

Concrete scenarios Babel's Hoard is built for, written from the point of view
of a developer: a senior engineer who runs local AI tools on Windows, working
from a FastAPI + React workspace with a large virtual environment, llama.cpp
and Ollama models. Two of them are driven by the assistant (Faustus) over
MCP, and one combines Babel with Faustus's own file tools.

Each scenario was walked for real (browser and MCP) on synthetic data; the
results are in [USABILITY_REPORT.md](USABILITY_REPORT.md). The data set is a
FastAPI + React project with a real virtual environment of about 200
installed distributions (fastapi, httpx, pydantic, pydantic-settings,
sqlalchemy, fastembed, psutil, numpy, pandas, chromadb, ...) and a
`frontend/node_modules` with React 19 and its type declarations.

---

## UC1 - Register my big project and see what is installed

- **Who:** the developer, in the browser, the first time they open Babel.
- **Goal:** tell Babel about `C:\Projects\myapp` and see which versions of
  the project's libraries are installed, without waiting for everything to be indexed.
- **Starting state:** fresh install; only Babel's own interpreter is known.
- **Steps:**
  1. Open Babel -> **Libraries**.
  2. Paste `C:\Projects\myapp` into "Add a project" and press **Add**.
  3. Open "Show installed packages", filter for `sqlalchemy`.
  4. Press **Index dependencies** and watch the progress bar.
- **Done when:** the project appears within a few seconds as the default
  environment, the package list shows the ~200 installed distributions with
  exact versions, and the dependency job shows visible, moving progress
  while the rest of the app stays usable.

## UC2 - Look up the exact signature before writing code

- **Who:** the developer, in the browser, while writing a new endpoint.
- **Goal:** know exactly what `sqlalchemy.ext.asyncio.async_sessionmaker`
  and `httpx.AsyncClient.stream` accept in the installed versions.
- **Starting state:** UC1 done; the packages are not indexed yet.
- **Steps:**
  1. **Search** -> type `async sessionmaker`.
  2. If nothing is found because SQLAlchemy is not indexed yet, follow the
     hint (or type the dotted name) to open the symbol.
  3. Read the parameters table, the defaults and the source location.
- **Done when:** the symbol panel shows the installed SQLAlchemy version's
  signature and parameters, and it is obvious what to do when a package is
  not indexed yet.

## UC3 - Check a model-written snippet by hand

- **Who:** the developer, in the browser, with code a chat model just produced.
- **Goal:** find hallucinated or outdated APIs before running the code.
- **Starting state:** UC1 done.
- **Steps:**
  1. **Check code** -> choose the project's environment.
  2. Paste a snippet that mixes pydantic v1 idioms (`class Config:
     orm_mode = True`, `.dict()`), a made-up `psutil.gpu_percent()` and an
     `httpx.AsyncClient(retries=3)`.
  3. Press **Check**.
- **Done when:** each problem is shown under its line with a message a
  person understands and a suggestion where one exists; correct lines are
  not flagged.

## UC4 - "Faustus, write it and prove it exists" (agent)

- **Who:** Faustus, running a local 27B model with a small context, asked:
  *"Faustus, escribe un endpoint FastAPI que haga streaming desde mi
  llama.cpp con httpx y comprueba que todo existe en mi proyecto
  C:\Projects\myapp."*
- **Goal:** the model writes code against the installed versions, not its
  training data.
- **Starting state:** Babel running; the project may or may not be
  registered yet.
- **Steps (tool calls):** `list_tools` -> `docs_libraries` (is the project
  registered? which env id?) -> `docs_add_environment(path)` if not ->
  `api_lookup("httpx.AsyncClient.stream", env)` -> write the endpoint ->
  `api_check_code(code, env)` -> fix every error -> check again.
- **Done when:** the final check is `ok: true` with no errors, every id or
  path from one result is usable in the next call, and all results fit
  comfortably in a small context.

## UC5 - "Faustus, review this whole module" (agent, zero false positives)

- **Who:** Faustus, asked: *"Faustus, revisa backend/app/chat_service.py y
  dime si usa alguna API que no exista en mi venv."*
- **Goal:** a trustworthy verdict on a real 300+ line module (FastAPI
  router, SQLAlchemy 2.0 async ORM, pydantic v2, pydantic-settings, httpx
  streaming, tenacity, fastembed, psutil, PyJWT, sse-starlette).
- **Starting state:** UC1 done; the module is correct (it is imported and
  exercised by its own tests).
- **Steps:** Faustus reads the file with its own file tool ->
  `api_check_code(code, env)` -> reports. Then the developer introduces three
  realistic mistakes and asks again.
- **Done when:** the correct module gives **zero** errors and zero
  warnings; the edited one reports exactly the three mistakes, with lines.

## UC6 - Learn an unfamiliar library, then write and check with Faustus's file tools (agent + Faustus tools)

- **Who:** Faustus, asked: *"Faustus, añade a mi proyecto un panel de
  memoria con fastembed: busca cómo se embebe una consulta, escribe
  backend/app/memory.py y compruébalo."*
- **Goal:** discover the right API by meaning, confirm its signature, write
  the file with Faustus's own file tools, verify it with Babel.
- **Starting state:** UC1 done; fastembed installed but maybe not indexed.
- **Steps:** `docs_search("query embedding", library="fastembed", env)` ->
  (no hits because it is not indexed: the result must say what to do) ->
  `api_lookup("fastembed.TextEmbedding.query_embed", env)` -> Faustus
  writes the file (its file tool) -> `api_check_code` -> done.
- **Done when:** the model reaches the right method in a few calls without
  guessing, and the empty search result tells it how to recover.

## UC7 - Check the React side too

- **Who:** the developer, in the browser.
- **Goal:** verify that the named imports in a component
  (`import { useState, useOptimistic } from "react"`, lucide icons) exist in
  the installed `frontend/node_modules`.
- **Starting state:** the project root was registered (UC1); the
  `node_modules` folder lives in `frontend/`.
- **Steps:** **Libraries** -> is the frontend detected? If not, add
  `C:\Projects\myapp\frontend` -> **Check code** -> language TypeScript ->
  paste the imports -> **Check**.
- **Done when:** a misspelled or non-existent import is flagged, and the
  Python environment of the same project keeps working as before.

## UC8 - My own project docs, searchable

- **Who:** the developer, in the browser.
- **Goal:** search the project's `docs/` folder (Markdown) next to the API
  index: "how do I start the backend", "MCP bridge".
- **Starting state:** UC1 done.
- **Steps:** **Docsets** -> "Index a folder" -> paste the `docs` path ->
  **Search** with the markdown filter.
- **Done when:** the right section is found and opens in full.
