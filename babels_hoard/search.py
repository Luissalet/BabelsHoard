"""FTS5 search, exact symbol lookup and library listings.

Every answer is computed from *current* indexes only: a library whose
status is ``superseded`` (an older installed version) never answers a
search or a lookup; it only shows up as "other versions" of a symbol.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from . import db, environments, indexing
from .symbols import Missing, SymbolIndex, parse_meta

_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[_\.\-/]+")
_CURRENT = "('done', 'partial')"
_KIND_SHORT = {
    "POSITIONAL_ONLY": "positional-only",
    "POSITIONAL_OR_KEYWORD": "positional-or-keyword",
    "KEYWORD_ONLY": "keyword-only",
    "VAR_POSITIONAL": "*args",
    "VAR_KEYWORD": "**kwargs",
}


def _clip(text: str | None, n: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= n else text[: n - 1] + "…"


def _tokenize_for_query(text: str) -> str:
    """Split camelCase / snake_case / dotted identifiers so a natural-language
    query still matches code identifiers, while keeping the original words.
    """
    terms: list[str] = []
    for word in text.split():
        terms.append(word)
        terms.extend(p for p in _CAMEL_SPLIT.split(word) if p)
    safe = sorted({re.sub(r'"', "", t) for t in terms if t.strip('"')})
    if not safe:
        return '""'
    return " OR ".join(f'"{t}"' for t in safe)


def _row_to_hit(row: dict[str, Any], lib: dict[str, Any] | None, score: float) -> dict[str, Any]:
    return {
        "id": row["id"],
        "qualname": row["qualname"],
        "kind": row["kind"],
        "library": f"{lib['name']}@{lib['version']}" if lib else None,
        "ecosystem": lib["ecosystem"] if lib else None,
        "env_id": lib.get("env_id") if lib else None,
        "signature": _clip(row.get("signature") or "", 200),
        "summary": _clip(row.get("summary") or "", 200),
        "score": round(score, 3),
    }


# Full-text rank alone puts constants and instance attributes first
# ("query embedding" -> Task.RETRIEVAL_QUERY before TextEmbedding.query_embed).
# What a person searching an API wants first is a callable or a class whose
# own name matches the words, reachable by a short public path.
_KIND_WEIGHT = {"class": 1.25, "function": 1.25, "method": 1.2, "module": 1.05, "property": 1.0, "attribute": 0.8}
_LEGACY_PARTS = {"v1", "deprecated", "compat", "legacy"}
_STOP = {"a", "an", "the", "to", "of", "in", "on", "for", "and", "or", "with", "by", "how", "do", "i", "is"}


def _query_words(query: str) -> list[str]:
    return [p.lower() for word in query.split() for p in _CAMEL_SPLIT.split(word) if p]


def _name_tokens(name: str) -> list[str]:
    return [t.lower() for t in _CAMEL_SPLIT.split(name) if t]


def _relevance(row: dict[str, Any], words: list[str]) -> float:
    kind = row.get("kind") or ""
    if kind == "section":
        return 1.0
    name = row["qualname"].rsplit(".", 1)[-1]
    weight = _KIND_WEIGHT.get(kind, 1.0)
    if len(name) > 1 and name.isupper():
        weight *= 0.7  # a constant
    wanted = [w for w in words if w not in _STOP and len(w) > 1]
    tokens = _name_tokens(name)
    if wanted and tokens:
        def matches(w: str) -> bool:
            return any(t == w or (min(len(t), len(w)) >= 4 and (t.startswith(w) or w.startswith(t))) for t in tokens)

        if "".join(wanted) == "".join(tokens):
            weight *= 2.0  # the name *is* the query ("async sessionmaker")
        elif all(matches(w) for w in wanted):
            weight *= 1.4
    depth = row["qualname"].count(".")
    weight *= max(0.8, 0.96 ** max(0, depth - 1))
    if any(part in _LEGACY_PARTS or part.startswith("_") for part in row["qualname"].split(".")[1:-1]):
        weight *= 0.85  # pydantic.v1.BaseModel, pkg._internal.x, pkg.deprecated.y
    return weight


def search(
    conn,
    query: str,
    *,
    library: str | None = None,
    ecosystem: str | None = None,
    kind: str | None = None,
    env: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    fts_query = _tokenize_for_query(query)
    filters = ""
    filter_params: list[Any] = []
    if library:
        filters += " AND (l.name = ? OR l.name = ?)"
        filter_params += [library, f"stdlib/{library}"]
    if ecosystem:
        filters += " AND l.ecosystem = ?"
        filter_params.append(ecosystem)
    if kind:
        filters += " AND e.kind = ?"
        filter_params.append(kind)
    if env:
        filters += " AND (l.env_id = ? OR l.env_id IS NULL)"
        filter_params.append(env)
    sql = f"""
        SELECT e.id, e.qualname, e.kind, e.signature, e.summary, e.library_id, e.target,
               bm25(entries_fts, 10.0, 6.0, 2.0, 2.0, 1.0) AS rank
        FROM entries_fts
        JOIN entries e ON e.rowid = entries_fts.rowid
        JOIN libraries l ON l.id = e.library_id
        WHERE entries_fts MATCH ? AND l.status IN {_CURRENT}
          AND e.name NOT IN ('__init__', '__call__', '__enter__', '__aenter__'){filters}
        ORDER BY rank LIMIT ?
    """
    try:
        fetched = [dict(r) for r in conn.execute(sql, [fts_query, *filter_params, limit * 4 + 1]).fetchall()]
    except Exception:
        fetched = []
    ident = query.strip()
    if ident.isidentifier():
        # An exact identifier ("Field", "FastAPI") must reach the ranking
        # even when long docstrings push it out of the full-text pool.
        seen_ids = {r["id"] for r in fetched}
        pool_best = min((r["rank"] for r in fetched), default=-10.0)
        exact_sql = f"""
            SELECT e.id, e.qualname, e.kind, e.signature, e.summary, e.library_id, e.target, ? AS rank
            FROM entries e JOIN libraries l ON l.id = e.library_id
            WHERE e.name = ? AND l.status IN {_CURRENT}{filters}
            ORDER BY length(e.qualname) LIMIT 20
        """
        try:
            for r in conn.execute(exact_sql, [pool_best, ident, *filter_params]).fetchall():
                if r["id"] not in seen_ids:
                    fetched.append(dict(r))
        except Exception:  # noqa: BLE001
            pass
        # every exact match starts level; kind, path depth and legacy paths decide
        for r in fetched:
            if r["qualname"].rsplit(".", 1)[-1] == ident:
                r["rank"] = pool_best
    # The same object re-exported under several paths (fastapi.sse.BaseModel
    # is pydantic's BaseModel) is one hit: keep the path in the package that
    # defines it, otherwise the best-ranked one.
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in fetched:
        key = r.get("target") or r["id"]
        native = bool(r.get("target")) and r["qualname"].split(".", 1)[0] == r["target"].split(".", 1)[0]
        r["_native"] = native
        if key not in best:
            best[key] = r
            order.append(key)
        elif native and not best[key]["_native"]:
            best[key] = r
        elif native == best[key]["_native"] and r["qualname"].count(".") < best[key]["qualname"].count("."):
            best[key] = {**r, "rank": min(r["rank"], best[key]["rank"])}  # the shortest public path
    rows = [best[k] for k in order]
    words = _query_words(query)
    for r in rows:
        r["_score"] = -r["rank"] * _relevance(r, words)
    rows.sort(key=lambda r: -r["_score"])
    lib_cache: dict[str, Any] = {}
    hits = []
    for row_d in rows[:limit]:
        lib_id = row_d["library_id"]
        if lib_id not in lib_cache:
            lib_cache[lib_id] = db.dump(conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())
        hits.append(_row_to_hit(row_d, lib_cache[lib_id], row_d["_score"]))
    out: dict[str, Any] = {
        "query": query,
        "results": hits,
        "count": len(hits),
        "truncated": len(rows) > limit,
    }
    if not hits:
        out["did_you_mean"] = did_you_mean(conn, query, env=env)
        if not out["did_you_mean"]:
            out["hint"] = (
                "Nothing indexed matches. api_lookup indexes a package on first use; "
                "docs_libraries shows what is indexed."
            )
    return out


def did_you_mean(conn, query: str, *, env: str | None = None, limit: int = 5) -> list[str]:
    """Typo-tolerant name suggestions from the trigram index."""
    words = [w for w in re.split(r"[^\w]+", query) if len(w) >= 3]
    grams = sorted({w[i : i + 3].lower() for w in words for i in range(len(w) - 2)})
    if not grams:
        return []
    match = " OR ".join('"' + g.replace('"', "") + '"' for g in grams[:40])
    sql = f"""
        SELECT e.qualname, e.name FROM names_trigram t
        JOIN entries e ON e.id = t.entry_id
        JOIN libraries l ON l.id = e.library_id
        WHERE names_trigram MATCH ? AND l.status IN {_CURRENT}
    """
    params: list[Any] = [match]
    if env:
        sql += " AND (l.env_id = ? OR l.env_id IS NULL)"
        params.append(env)
    sql += " ORDER BY bm25(names_trigram) LIMIT 200"
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []
    by_name: dict[str, str] = {}
    for r in rows:
        by_name.setdefault(r["name"], r["qualname"])
    target = words[-1] if words else query
    close = difflib.get_close_matches(target, list(by_name), n=limit, cutoff=0.6)
    return [by_name[n] for n in close]


# ------------------------------------------------------------------ lookup --
def _compact_params(params: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    out = []
    for p in params[:limit]:
        item = {"name": p["name"], "kind": _KIND_SHORT.get(p.get("kind") or "", p.get("kind"))}
        if p.get("annotation"):
            item["annotation"] = _clip(p["annotation"], 100)
        if p.get("default") is not None:
            item["default"] = _clip(p["default"], 60)
        else:
            if p.get("kind") in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD", "KEYWORD_ONLY"):
                item["required"] = True
        if p.get("description"):
            item["description"] = _clip(p["description"], 200)
        out.append(item)
    return out


def _entry_payload(conn, sym: SymbolIndex | None, e: dict[str, Any], *, doc_chars: int) -> dict[str, Any]:
    meta = parse_meta(e)
    params = db.from_json(e.get("params_json")) or []
    if e["kind"] == "method" and meta.get("mt") in ("i", "c") and params and params[0]["name"] in ("self", "cls"):
        params = params[1:]
    doc = e.get("doc") or ""
    doc_chars = max(0, doc_chars)
    payload: dict[str, Any] = {
        "found": True,
        "id": e["id"],
        "qualname": e["qualname"],
        "kind": e["kind"],
        "signature": e.get("signature"),
        "params": _compact_params(params),
        "returns": _clip(e.get("returns"), 300),
        "summary": _clip(e.get("summary"), 300),
        "doc": doc[:doc_chars] if doc_chars else None,
        "doc_truncated": len(doc) > doc_chars,
        "deprecated": bool(e.get("deprecated")),
        "source": (f"{e['source_path']}:{e['source_line']}" if e.get("source_line") else e.get("source_path")),
        "library": f"{e.get('lib_name')}@{e.get('lib_version')}",
    }
    if len(params) > 40:
        payload["params_truncated"] = True
    if e["kind"] == "class" and params:
        payload["params_are"] = "constructor (__init__) parameters"
    if payload["doc_truncated"]:
        payload["next"] = f"docs_read(id='{e['id']}', offset={doc_chars}) for the rest of the doc"
    if e.get("deprecated"):
        payload["deprecated_note"] = e.get("deprecated_note")
    if meta.get("dyn") or meta.get("nx") or meta.get("trunc"):
        payload["note"] = (
            "This namespace is only partly known statically (dynamic attributes, compiled code or a size cap)."
            if e["kind"] in ("module", "class")
            else None
        )
        if payload["note"] is None:
            payload.pop("note")
    if meta.get("see"):
        payload["same_as"] = meta["see"]
    if sym is not None and e["kind"] in ("module", "class"):
        names = [n for n in sym.children(e, limit=400) if not n.startswith("_")]
        payload["members"] = names[:25]
        payload["members_total"] = len(names)
    # Other indexed versions of the same symbol (older installs)
    if e.get("lib_name"):
        others = conn.execute(
            """
            SELECT DISTINCT l.version FROM entries x JOIN libraries l ON l.id = x.library_id
            WHERE x.qualname = ? AND l.name = ? AND l.id != ? AND l.ecosystem = ?
            LIMIT 5
            """,
            (e["qualname"], e["lib_name"], e["library_id"], e.get("lib_eco") or "python"),
        ).fetchall()
        if others:
            payload["other_versions_indexed"] = [r["version"] for r in others]
    return payload


def _python_lookup(conn, symbol: str, env_row: dict[str, Any], doc_chars: int) -> dict[str, Any] | None:
    sym = SymbolIndex(conn, env_row)
    top = symbol.split(".")[0]
    lib = sym.library_for(top)
    if lib is None:
        return None
    found = sym.resolve(symbol)
    if isinstance(found, dict):
        found.setdefault("lib_eco", "python")
        payload = _entry_payload(conn, sym, found, doc_chars=doc_chars)
        payload["env"] = env_row["id"]
        return payload
    label = f"{lib['name']} {lib['version']}" if not lib["name"].startswith("stdlib/") else f"the Python {lib['version']} stdlib"
    where = env_row.get("label") or env_row["id"]
    if isinstance(found, Missing):
        parent = found.parent
        names = [n for n in sym.children(parent) if not n.startswith("_")]
        suggestions = difflib.get_close_matches(found.name, names, n=5, cutoff=0.5)
        if found.certainty == "error":
            message = f"'{symbol}' does not exist in {label} installed in {where}."
        else:
            message = (
                f"'{symbol}' is not in the static index of {label} ({where}); '{parent['qualname']}' "
                "creates some names dynamically or is only partly indexed, so it may still exist at runtime."
            )
        return {
            "found": False,
            "symbol": symbol,
            "certain": found.certainty == "error",
            "closest_parent": parent["qualname"],
            "suggestions": [f"{sym.home(parent)}.{s}" for s in suggestions],
            "message": message,
            "library": f"{lib['name']}@{lib['version']}",
            "env": env_row["id"],
        }
    return None


def _js_lookup(conn, symbol: str, env_row: dict[str, Any], doc_chars: int) -> dict[str, Any] | None:
    from . import node_indexing

    if not env_row.get("node_modules_path"):
        return None
    if symbol.startswith("@"):
        scope, _, rest = symbol.partition("/")
        pkg_tail, _, member = rest.partition(".")
        pkg = f"{scope}/{pkg_tail}"
    else:
        pkg, _, member = symbol.partition(".")
    lib = node_indexing.current_js_library(conn, env_row, pkg)
    if lib is None or lib["status"] not in ("done", "partial"):
        return None
    qual = f"{pkg}.{member}" if member else pkg
    row = conn.execute(
        "SELECT e.*, ? AS lib_name, ? AS lib_version, 'js' AS lib_eco FROM entries e WHERE e.library_id=? AND e.qualname=?",
        (lib["name"], lib["version"], lib["id"], qual),
    ).fetchone()
    if row is not None:
        payload = _entry_payload(conn, None, dict(row), doc_chars=doc_chars)
        payload["env"] = env_row["id"]
        return payload
    names = [r["name"] for r in conn.execute("SELECT name FROM entries WHERE library_id=? AND parent_id IS NULL", (lib["id"],))]
    return {
        "found": False,
        "symbol": symbol,
        "certain": lib["status"] == "done",
        "suggestions": [f"{pkg}.{n}" for n in difflib.get_close_matches(member, names, n=5, cutoff=0.5)],
        "message": f"'{member}' is not exported by {pkg} {lib['version']} installed in {env_row.get('label')}.",
        "library": f"{lib['name']}@{lib['version']}",
        "env": env_row["id"],
    }


def lookup_with_lazy_index(
    conn, symbol: str, *, env: str | None = None, library: str | None = None, doc_chars: int = 1500
) -> dict[str, Any]:
    """``api_lookup``: the exact installed signature/doc of ``symbol``.

    Indexes the package (or its newly installed version) on first need.
    """
    symbol = symbol.strip().strip("`").removesuffix("()")
    # Without env, Python symbols are answered by the default Python
    # environment and npm symbols by the default node_modules one.
    env_row = environments.resolve_env(conn, env, "python")
    js_env_row = env_row if env else environments.resolve_env(conn, None, "typescript")
    if library and library.lower() in ("js", "npm"):
        library = None
    result = None
    if not symbol.startswith("@"):
        result = _python_lookup(conn, symbol, env_row, doc_chars)
    if result is None:
        result = _js_lookup(conn, symbol, js_env_row, doc_chars)
    if result is not None:
        return result
    top = symbol.split(".")[0]
    installed: list[str] = []
    if env_row.get("python_path"):
        try:
            probe = environments.probe_python(__import__("pathlib").Path(env_row["python_path"]))
            for dist in probe.get("distributions", []):
                installed.extend(indexing.import_names_for(probe, dist))
        except Exception:
            pass
    close = difflib.get_close_matches(top, sorted(set(installed)), n=3, cutoff=0.8)
    if not env_row.get("python_path"):
        message = (
            f"'{top}' is not an npm package in {env_row.get('label')}, and this environment has no Python "
            "interpreter (node_modules only). For a Python symbol pass the env id of the project's Python "
            "environment (docs_libraries lists them), or omit env."
        )
    else:
        message = (
            f"'{top}' is not installed (or not importable) in {env_row.get('label')}"
            f"{' (Python ' + env_row['python_version'] + ')' if env_row.get('python_version') else ''}. "
            "If it belongs to another project, register it with docs_add_environment and pass env."
        )
    return {
        "found": False,
        "symbol": symbol,
        "certain": False,
        "suggestions": close,
        "message": message,
        "env": env_row["id"],
    }


# --------------------------------------------------------------- listings --
def libraries_overview(
    conn, *, ecosystem: str | None = None, env: str | None = None, limit: int = 30, offset: int = 0
) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    where = [f"status IN {_CURRENT}"]
    params: list[Any] = []
    if ecosystem:
        where.append("ecosystem=?")
        params.append(ecosystem)
    if env:
        where.append("(env_id=? OR env_id IS NULL)")
        params.append(env)
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) c FROM libraries WHERE {clause}", params).fetchone()["c"]
    rows = conn.execute(
        f"SELECT id, ecosystem, name, version, env_id, status, entry_count FROM libraries WHERE {clause} "
        "ORDER BY ecosystem, name LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    libs = [
        {
            "id": r["id"],
            "ecosystem": r["ecosystem"],
            "name": r["name"],
            "version": r["version"],
            "env": r["env_id"],
            "status": r["status"],
            "entries": r["entry_count"],
        }
        for r in rows
    ]
    has_more = offset + len(libs) < total
    return {
        "libraries": libs,
        "total": total,
        "has_more": has_more,
        "next_offset": offset + len(libs) if has_more else None,
        "note": "Packages installed but not listed here are indexed automatically on first api_lookup/api_check_code.",
    }


def list_libraries(conn, *, ecosystem: str | None = None, env: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM libraries WHERE 1=1"
    params: list[Any] = []
    if ecosystem:
        sql += " AND ecosystem=?"
        params.append(ecosystem)
    if env:
        sql += " AND env_id=?"
        params.append(env)
    sql += " ORDER BY ecosystem, name, created_at DESC"
    return db.dump_all(conn.execute(sql, params).fetchall())


def read_entry(conn, entry_id: str, offset: int = 0, max_chars: int = 4000) -> dict[str, Any]:
    row = conn.execute(
        "SELECT e.*, l.name AS lib_name, l.version AS lib_version FROM entries e "
        "JOIN libraries l ON l.id = e.library_id WHERE e.id=?",
        (entry_id,),
    ).fetchone()
    if row is None:
        return {
            "found": False,
            "id": entry_id,
            "message": "No entry with this id. Ids come from docs_search results or api_lookup (field 'id').",
        }
    d = dict(row)
    # Offsets index the doc text itself, so api_lookup's "next" offset lines up.
    text = d.get("doc") or d.get("summary") or ""
    offset = max(0, offset)
    max_chars = max(1, min(max_chars, 20000))
    chunk = text[offset : offset + max_chars]
    has_more = offset + max_chars < len(text)
    return {
        "found": True,
        "id": entry_id,
        "qualname": d["qualname"],
        "kind": d["kind"],
        "library": f"{d['lib_name']}@{d['lib_version']}",
        "signature": _clip(d.get("signature"), 400) if offset == 0 else None,
        "text": chunk,
        "offset": offset,
        "total_chars": len(text),
        "has_more": has_more,
        "next_offset": offset + max_chars if has_more else None,
    }
