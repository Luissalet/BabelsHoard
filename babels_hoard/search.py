"""FTS5 search and exact symbol lookup."""
from __future__ import annotations

import difflib
import re
from typing import Any

from . import db

_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|[_\.]+")


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


def _row_to_hit(row: dict[str, Any], lib: dict[str, Any], score: float) -> dict[str, Any]:
    sig = row.get("signature") or ""
    summary = row.get("summary") or ""
    return {
        "id": row["id"],
        "qualname": row["qualname"],
        "kind": row["kind"],
        "library": f"{lib['name']}@{lib['version']}" if lib else None,
        "ecosystem": lib["ecosystem"] if lib else None,
        "env_id": lib.get("env_id") if lib else None,
        "signature": sig[:200] + ("…" if len(sig) > 200 else ""),
        "summary": summary[:200] + ("…" if len(summary) > 200 else ""),
        "score": round(score, 3),
    }


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
    sql = """
        SELECT e.*, bm25(entries_fts, 10.0, 6.0, 2.0, 2.0, 1.0) AS rank
        FROM entries_fts
        JOIN entries e ON e.rowid = entries_fts.rowid
        JOIN libraries l ON l.id = e.library_id
        WHERE entries_fts MATCH ?
    """
    params: list[Any] = [fts_query]
    if library:
        sql += " AND l.name = ?"
        params.append(library)
    if ecosystem:
        sql += " AND l.ecosystem = ?"
        params.append(ecosystem)
    if kind:
        sql += " AND e.kind = ?"
        params.append(kind)
    if env:
        sql += " AND l.env_id = ?"
        params.append(env)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit + 1)
    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:
        rows = []
    lib_cache: dict[str, Any] = {}
    hits = []
    for row in rows[:limit]:
        row_d = dict(row)
        lib_id = row_d["library_id"]
        if lib_id not in lib_cache:
            lib_cache[lib_id] = db.dump(
                conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
            )
        hits.append(_row_to_hit(row_d, lib_cache[lib_id], -row["rank"]))
    return {
        "query": query,
        "results": hits,
        "count": len(hits),
        "truncated": len(rows) > limit,
    }


def suggest_similar(conn, symbol: str, parent_qualname: str | None, limit: int = 5) -> list[str]:
    """Closest real names for a not-found lookup: difflib over siblings, then
    a substring/trigram-ish fallback over all entry names."""
    candidates: list[str] = []
    if parent_qualname:
        rows = conn.execute(
            "SELECT qualname FROM entries WHERE qualname LIKE ? LIMIT 500",
            (parent_qualname + ".%",),
        ).fetchall()
        candidates = [r["qualname"].rsplit(".", 1)[-1] for r in rows]
    if not candidates:
        rows = conn.execute("SELECT DISTINCT name FROM entries LIMIT 5000").fetchall()
        candidates = [r["name"] for r in rows]
    leaf = symbol.rsplit(".", 1)[-1]
    close = difflib.get_close_matches(leaf, candidates, n=limit, cutoff=0.5)
    return close


def api_lookup(conn, symbol: str, *, env_row: dict[str, Any] | None, library: str | None = None) -> dict[str, Any]:
    sql = "SELECT e.*, l.name AS lib_name, l.version AS lib_version, l.ecosystem AS lib_eco, l.env_id AS lib_env FROM entries e JOIN libraries l ON l.id = e.library_id WHERE e.qualname = ?"
    params: list[Any] = [symbol]
    if env_row:
        sql += " AND l.env_id = ?"
        params.append(env_row["id"])
    if library:
        sql += " AND l.name = ?"
        params.append(library)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        parent = symbol.rsplit(".", 1)[0] if "." in symbol else None
        suggestions = suggest_similar(conn, symbol, parent)
        lib_desc = library or (parent.split(".")[0] if parent else symbol.split(".")[0])
        version_desc = "the registered environment" if env_row is None else env_row.get("label", env_row["id"])
        return {
            "found": False,
            "symbol": symbol,
            "suggestions": suggestions,
            "message": f"'{symbol}' does not exist in {lib_desc} installed in {version_desc}.",
        }
    d = dict(row)
    return {
        "found": True,
        "symbol": symbol,
        "qualname": d["qualname"],
        "kind": d["kind"],
        "signature": d["signature"],
        "params": db.from_json(d["params_json"]) or [],
        "returns": d["returns"],
        "summary": d["summary"],
        "doc": d["doc"],
        "deprecated": bool(d["deprecated"]),
        "deprecated_note": d["deprecated_note"],
        "source_path": d["source_path"],
        "source_line": d["source_line"],
        "library": f"{d['lib_name']}@{d['lib_version']}",
        "ecosystem": d["lib_eco"],
        "env_id": d["lib_env"],
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
    sql += " ORDER BY ecosystem, name"
    return db.dump_all(conn.execute(sql, params).fetchall())


def read_entry(conn, entry_id: str, offset: int = 0, max_chars: int = 4000) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        return {"found": False, "id": entry_id}
    d = dict(row)
    text = d.get("doc") or d.get("summary") or ""
    offset = max(0, offset)
    max_chars = max(200, min(max_chars, 20000))
    chunk = text[offset : offset + max_chars]
    return {
        "found": True,
        "id": entry_id,
        "qualname": d["qualname"],
        "kind": d["kind"],
        "text": chunk,
        "offset": offset,
        "total_chars": len(text),
        "has_more": offset + max_chars < len(text),
        "next_offset": offset + max_chars if offset + max_chars < len(text) else None,
    }
