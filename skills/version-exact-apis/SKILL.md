---
name: version-exact-apis
description: Use Babel's Hoard to look up exact, version-installed API signatures and to check code for hallucinated or removed APIs, instead of relying on training-data memory of a library's interface.
---

# Version-exact APIs with Babel's Hoard

Your training data is a blur of library versions. The project's own
`.venv` / `node_modules` on disk is the ground truth for what actually
exists. Babel's Hoard reads it and answers with the real, installed
signature.

## When to reach for it

- Before calling any function/method from a third-party library (pandas,
  fastapi, react, ...) that you are not 100% certain about in *this*
  project's installed version -> `api_lookup`.
- After writing a non-trivial snippet that uses external libraries, before
  showing it to the human -> `api_check_code`. This catches renamed
  parameters, removed methods, and typos in attribute names.
- When you don't know the exact symbol name, only roughly what it does ->
  `docs_search` first, then `api_lookup` on the result's qualname.
- Before working in a project you have not touched yet in this session ->
  `docs_add_environment` with the project's path, so lookups resolve
  against its real dependencies instead of nothing.

## Order that works

1. `docs_add_environment(path)` once per project (skip if already
   registered - check `docs_libraries` first).
2. `api_lookup(symbol, env)` for anything you are about to call. If
   `found: false`, read the `suggestions` - that is what actually exists,
   use it instead of guessing again.
3. Write the code.
4. `api_check_code(code, env)` on what you wrote. If `ok: false`, fix every
   `error`-severity finding before presenting the code. `warning` findings
   (deprecated APIs) are worth a mention but not blocking.
5. For a symbol you can't find by exact name, `docs_search(query)` first.

## Traps

- `api_lookup`/`api_check_code` index a package **lazily on first use** -
  the first call for a never-seen library takes a little longer than
  later ones. This is normal, not a failure.
- `unchecked` in `api_check_code`'s result is not "correct" - it means
  Babel could not verify that part (dynamic code, an unresolved type).
  Zero false positives is the design goal, so treat `unchecked` as "no
  information", not as a pass.
- A `docsets`/`markdown` result's `kind` is `"section"`, not a code symbol -
  read it with `docs_read`, don't feed it to `api_lookup`.
- If every tool call fails with `babel_unavailable`, the app itself is not
  running - say so plainly rather than retrying silently.
