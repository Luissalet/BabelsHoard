"""Environment-scoped symbol resolution over the current Python indexes.

Shared by ``api_lookup`` and ``api_check_code`` so both answer the same
question the same way: *does ``a.b.c`` exist in the version installed in
this environment, and if not, can we say so with certainty?*

Only libraries whose status is ``done``/``partial`` count - a ``superseded``
index (an older installed version) is kept for the UI's "other versions"
but never used for answers.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

from . import db, indexing

CURRENT = ("done", "partial")


def parse_meta(row: dict[str, Any]) -> dict[str, Any]:
    meta = row.get("_meta")
    if meta is None:
        meta = db.from_json(row.get("meta_json")) or {}
        row["_meta"] = meta
    return meta


@dataclass
class Missing:
    """``name`` is not a known member of ``parent``."""

    parent: dict[str, Any]
    name: str
    certainty: str  # "error" (namespace fully known) | "warning" (soft-dynamic) | "unknown"


class SymbolIndex:
    def __init__(self, conn, env_row: dict[str, Any], *, lazy: bool = True):
        self.conn = conn
        self.env = env_row
        self.lazy = lazy
        self._entry_cache: dict[str, dict[str, Any] | None] = {}
        self._lib_cache: dict[str, dict[str, Any] | None] = {}
        self._libs_by_id: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------ libraries
    def library_for(self, top: str) -> dict[str, Any] | None:
        """The current library that provides import name ``top`` (indexing
        it on first need when ``lazy``)."""
        if top in self._lib_cache:
            return self._lib_cache[top]
        lib = None
        if self.lazy:
            lib = indexing.current_library(self.conn, self.env, top)
        if lib is None:
            row = self.conn.execute(
                """
                SELECT l.* FROM entries e JOIN libraries l ON l.id = e.library_id
                WHERE e.qualname = ? AND e.kind = 'module' AND l.env_id = ?
                  AND l.ecosystem = 'python' AND l.status IN ('done', 'partial')
                LIMIT 1
                """,
                (top, self.env["id"]),
            ).fetchone()
            lib = db.dump(row)
        self._lib_cache[top] = lib
        if lib:
            self._libs_by_id[lib["id"]] = lib
        return lib

    def library(self, lib_id: str) -> dict[str, Any] | None:
        if lib_id not in self._libs_by_id:
            row = self.conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
            if row is None:
                return None
            self._libs_by_id[lib_id] = db.dump(row)
        return self._libs_by_id[lib_id]

    # -------------------------------------------------------------- entries
    def entry(self, qualname: str) -> dict[str, Any] | None:
        if qualname in self._entry_cache:
            return self._entry_cache[qualname]
        row = self.conn.execute(
            """
            SELECT e.*, l.name AS lib_name, l.version AS lib_version, l.status AS lib_status
            FROM entries e JOIN libraries l ON l.id = e.library_id
            WHERE e.qualname = ? AND l.env_id = ? AND l.ecosystem = 'python'
              AND l.status IN ('done', 'partial')
            LIMIT 1
            """,
            (qualname, self.env["id"]),
        ).fetchone()
        result = dict(row) if row else None
        self._entry_cache[qualname] = result
        return result

    def home(self, e: dict[str, Any]) -> str:
        return parse_meta(e).get("see") or e["qualname"]

    def child(self, e: dict[str, Any], name: str) -> dict[str, Any] | Missing:
        found = self.entry(f"{self.home(e)}.{name}")
        if found is not None:
            return found
        return Missing(e, name, self.certainty_of_absence(e, name))

    def certainty_of_absence(self, e: dict[str, Any], name: str) -> str:
        """How sure we are that ``name`` is *not* a member of ``e``."""
        if name.startswith("_"):
            return "unknown"  # private / dunder names are not indexed
        if e["kind"] not in ("module", "class"):
            return "unknown"
        home = self.entry(self.home(e)) or e
        meta = parse_meta(home)
        if meta.get("nx") or meta.get("trunc") or meta.get("unres") or meta.get("compiled"):
            return "unknown"
        dyn = meta.get("dyn")
        if dyn == 1:
            return "unknown"
        if dyn == "soft":
            return "warning"
        return "error"

    def resolve(self, dotted: str) -> dict[str, Any] | Missing | None:
        """Resolve a dotted path by walking member by member (following
        ``see`` homes). ``None`` when the top-level name is unknown."""
        direct = self.entry(dotted)
        if direct is not None:
            return direct
        parts = dotted.split(".")
        cur = self.entry(parts[0])
        if cur is None:
            return None
        for seg in parts[1:]:
            nxt = self.child(cur, seg)
            if isinstance(nxt, Missing):
                return nxt
            cur = nxt
        return cur

    def children(self, e: dict[str, Any], limit: int = 2000) -> list[str]:
        home = self.home(e)
        rows = self.conn.execute(
            """
            SELECT e.name FROM entries e JOIN libraries l ON l.id = e.library_id
            WHERE e.parent_id = (SELECT e2.id FROM entries e2 JOIN libraries l2 ON l2.id = e2.library_id
                                 WHERE e2.qualname = ? AND l2.env_id = ? AND l2.status IN ('done','partial') LIMIT 1)
              AND l.status IN ('done', 'partial')
            LIMIT ?
            """,
            (home, self.env["id"], limit),
        ).fetchall()
        return [r["name"] for r in rows]

    def suggest(self, e: dict[str, Any], name: str) -> str | None:
        names = [n for n in self.children(e) if not n.startswith("_")]
        close = difflib.get_close_matches(name, names, n=1, cutoff=0.72)
        if close:
            return close[0]
        lowered = {n.lower(): n for n in names}
        return lowered.get(name.lower())

    def class_by_target(self, target: str) -> dict[str, Any] | None:
        """The home entry of the class whose canonical path is ``target``."""
        key = "@target:" + target
        if key in self._entry_cache:
            return self._entry_cache[key]
        top = target.split(".", 1)[0]
        self.library_for(top)
        rows = self.conn.execute(
            """
            SELECT e.*, l.name AS lib_name, l.version AS lib_version, l.status AS lib_status
            FROM entries e JOIN libraries l ON l.id = e.library_id
            WHERE e.target = ? AND e.kind = 'class' AND l.env_id = ?
              AND l.ecosystem = 'python' AND l.status IN ('done', 'partial')
            ORDER BY length(e.qualname)
            LIMIT 5
            """,
            (target, self.env["id"]),
        ).fetchall()
        result = None
        for r in rows:
            d = dict(r)
            if not parse_meta(d).get("see"):
                result = d
                break
        if result is None and rows:
            result = self.entry(parse_meta(dict(rows[0])).get("see") or rows[0]["qualname"])
        self._entry_cache[key] = result
        return result
