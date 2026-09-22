"""Static Python indexing with griffe. Babel never imports or executes the
target project's code: griffe parses source (AST) for pure-Python packages,
and compiled modules without ``.pyi`` stubs are recorded honestly rather than
invented.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import griffe

from . import db

logger = logging.getLogger("babels_hoard.indexing")

MAX_ENTRIES_PER_LIBRARY = 1500
MAX_DEPTH = 8
DOC_CAP = 8000
DOCSTRING_PARSERS = ("google", "numpy", "sphinx")

_KIND_MAP = {
    "positional-only": "POSITIONAL_ONLY",
    "positional or keyword": "POSITIONAL_OR_KEYWORD",
    "variadic positional": "VAR_POSITIONAL",
    "keyword-only": "KEYWORD_ONLY",
    "variadic keyword": "VAR_KEYWORD",
}


class IndexError_(RuntimeError):
    pass


def library_id(ecosystem: str, name: str, version: str, source: str) -> str:
    raw = f"{ecosystem}:{name}:{version}:{source}"
    return "lib-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def find_distribution(probe: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Match an import name (e.g. ``pandas``) to a probed distribution.

    Two passes: an exact distribution-name match always wins (some
    distributions - e.g. griffe 2.x split into griffe/griffelib/griffecli -
    all declare the same top_level import name; only one of them is the
    package you'd actually ``pip install``), falling back to a top_level
    scan only when nothing is named after the import itself.
    """
    lname = name.lower()
    distributions = probe.get("distributions", [])
    for dist in distributions:
        if dist["name"].lower() == lname or dist["name"].lower().replace("-", "_") == lname:
            return dist
    for dist in distributions:
        if lname in [t.lower() for t in dist.get("top_level", [])]:
            return dist
    return None


def _classify(obj: Any, parent: Any) -> str:
    labels = getattr(obj, "labels", set()) or set()
    kind = obj.kind.value if hasattr(obj.kind, "value") else str(obj.kind)
    if "property" in labels:
        return "property"
    if kind == "function":
        if parent is not None and getattr(parent, "kind", None) is not None:
            pkind = parent.kind.value if hasattr(parent.kind, "value") else str(parent.kind)
            if pkind == "class":
                return "method"
        return "function"
    if kind in ("module", "class", "attribute"):
        return kind
    return kind


def _stringify(expr: Any) -> str | None:
    if expr is None:
        return None
    try:
        return str(expr)
    except Exception:
        return None


def _docstring_param_notes(obj: Any) -> tuple[str | None, dict[str, str], str | None]:
    """Returns (summary, {param_name: description}, returns_text) parsed from
    the docstring, trying google/numpy/sphinx and keeping whichever finds the
    most parameter descriptions."""
    doc = getattr(obj, "docstring", None)
    if doc is None or not doc.value:
        return None, {}, None
    raw = doc.value
    summary = raw.strip().splitlines()[0].strip() if raw.strip() else None
    best_notes: dict[str, str] = {}
    returns_text = None
    best_count = -1
    for parser in DOCSTRING_PARSERS:
        try:
            sections = doc.parse(parser)
        except Exception:
            continue
        local_notes: dict[str, str] = {}
        local_returns = None
        for section in sections:
            kind = section.kind.value if hasattr(section.kind, "value") else str(section.kind)
            if kind == "parameters":
                for p in section.value:
                    d = (getattr(p, "description", "") or "").strip()
                    if d:
                        local_notes[p.name] = d
            elif kind == "returns":
                parts = [
                    (getattr(r, "description", "") or "").strip()
                    for r in section.value
                    if (getattr(r, "description", "") or "").strip()
                ]
                if parts:
                    local_returns = " ".join(parts)
        if len(local_notes) > best_count:
            best_count = len(local_notes)
            best_notes = local_notes
            if local_returns:
                returns_text = local_returns
    return summary, best_notes, returns_text


def _deprecation_of(obj: Any) -> tuple[bool, str | None]:
    dep = getattr(obj, "deprecated", None)
    if dep:
        return True, getattr(dep, "message", None) or _stringify(dep) or "deprecated"
    return False, None


def _signature_params(obj: Any, notes: dict[str, str]) -> list[dict[str, Any]]:
    params = getattr(obj, "parameters", None)
    if not params:
        return []
    out = []
    for p in params:
        kind_raw = p.kind.value if hasattr(p.kind, "value") else str(p.kind)
        out.append(
            {
                "name": p.name,
                "kind": _KIND_MAP.get(kind_raw, kind_raw),
                "annotation": _stringify(p.annotation),
                "default": _stringify(p.default),
                "description": notes.get(p.name),
            }
        )
    return out


def _build_signature(name: str, obj: Any, kind: str) -> str:
    if kind in ("function", "method"):
        params = getattr(obj, "parameters", None)
        parts = []
        if params:
            for p in params:
                piece = p.name
                if p.annotation is not None:
                    piece += f": {_stringify(p.annotation)}"
                if p.default is not None:
                    piece += f" = {_stringify(p.default)}"
                parts.append(piece)
        ret = _stringify(getattr(obj, "returns", None))
        sig = f"{name}({', '.join(parts)})"
        if ret:
            sig += f" -> {ret}"
        return sig
    if kind == "class":
        bases = getattr(obj, "bases", None) or []
        base_str = ", ".join(_stringify(b) or "" for b in bases)
        return f"class {name}({base_str})" if base_str else f"class {name}"
    if kind in ("attribute", "property"):
        ann = _stringify(getattr(obj, "annotation", None))
        val = _stringify(getattr(obj, "value", None))
        sig = name
        if ann:
            sig += f": {ann}"
        if val:
            sig += f" = {val}"
        return sig
    return name


class _Budget:
    def __init__(self, cap: int):
        self.cap = cap
        self.count = 0
        self.truncated = False


def _iter_members(obj: Any) -> list[tuple[str, Any]]:
    """Own members plus (for classes) inherited members not overridden
    locally, so a subclass's entries include what it actually responds to."""
    members = getattr(obj, "members", None) or {}
    items = list(members.items())
    kind = obj.kind.value if hasattr(obj.kind, "value") else str(obj.kind)
    if kind == "class":
        try:
            inherited = obj.inherited_members
        except Exception:
            inherited = {}
        for name, member in (inherited or {}).items():
            if name not in members:
                items.append((name, member))
    return items


def _walk(
    obj: Any,
    public_path: str,
    parent: Any,
    depth: int,
    budget: _Budget,
    ancestors: frozenset,
    rows: list[tuple],
    library_id_: str,
):
    if budget.count >= budget.cap:
        budget.truncated = True
        return
    exports = getattr(obj, "exports", None)
    export_names = set(exports) if exports else None
    for name, member in _iter_members(obj):
        if budget.count >= budget.cap:
            budget.truncated = True
            return
        if name.startswith("_") and (export_names is None or name not in export_names):
            continue
        child_path = f"{public_path}.{name}"
        try:
            resolved = member.final_target if getattr(member, "is_alias", False) else member
        except Exception:
            # A real name that griffe could not statically resolve (e.g. a
            # conditional cross-module alias like ``os.path``). Record a
            # bare marker entry so the name is known to *exist* - deeper
            # lookups under it correctly fall back to "unchecked" instead
            # of a false "unknown_attribute".
            rows.append(
                (
                    f"{library_id_}:{child_path}",
                    library_id_,
                    name,
                    child_path,
                    "attribute",
                    f"{name} (unresolved alias)",
                    None,
                    None,
                    None,
                    "Babel could not statically resolve this name to a single target (a conditional or dynamic alias); nothing further is known about it.",
                    0,
                    None,
                    None,
                    None,
                    None,
                )
            )
            budget.count += 1
            continue
        if resolved is None:
            continue
        oid = id(resolved)
        kind = _classify(resolved, obj)
        if kind not in ("module", "class", "function", "method", "attribute", "property"):
            continue
        summary, notes, returns_text = _docstring_param_notes(resolved)
        params = _signature_params(resolved, notes) if kind in ("function", "method") else []
        deprecated, dep_note = _deprecation_of(resolved)
        doc_val = getattr(resolved, "docstring", None)
        full_doc = (doc_val.value if doc_val else None) or None
        if full_doc and len(full_doc) > DOC_CAP:
            full_doc = full_doc[:DOC_CAP] + "\n... (truncated)"
        note = None
        filepath = getattr(resolved, "filepath", None)
        lineno = getattr(resolved, "lineno", None)
        if kind == "module" and filepath is None:
            note = "compiled, no stubs"
        signature = _build_signature(name, resolved, kind)
        rows.append(
            (
                f"{library_id_}:{child_path}",
                library_id_,
                name,
                child_path,
                kind,
                signature,
                db.to_json(params) if params else None,
                returns_text,
                summary,
                full_doc,
                int(deprecated),
                dep_note,
                str(filepath) if filepath else None,
                lineno,
                None,
            )
        )
        budget.count += 1
        if kind in ("module", "class") and depth < MAX_DEPTH and oid not in ancestors:
            _walk(resolved, child_path, resolved, depth + 1, budget, ancestors | {oid}, rows, library_id_)


def index_python_library(
    conn,
    *,
    env: dict[str, Any],
    probe: dict[str, Any],
    import_name: str,
    dist_name: str | None = None,
    version: str | None = None,
    search_paths: list[str] | None = None,
    force: bool = False,
    source: str | None = None,
) -> dict[str, Any]:
    source = source or f"env:{env['id']}"
    dist = find_distribution(probe, import_name) if dist_name is None else None
    if dist is not None:
        dist_name = dist["name"]
        version = dist["version"]
    if version is None:
        version = "0.0.0"
    if dist_name is None:
        dist_name = import_name

    lib_id = library_id("python", dist_name, version, source)
    existing = conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
    if existing and existing["status"] in ("done", "partial") and not force:
        return db.dump(existing)

    conn.execute(
        """
        INSERT INTO libraries (id, ecosystem, name, version, source, env_id, status, created_at)
        VALUES (?, 'python', ?, ?, ?, ?, 'indexing', ?)
        ON CONFLICT(id) DO UPDATE SET status='indexing'
        """,
        (lib_id, dist_name, version, source, env["id"], db.now()),
    )
    conn.commit()

    paths = search_paths or probe.get("sys_path") or []
    try:
        root = griffe.load(
            import_name,
            search_paths=paths,
            allow_inspection=False,
            resolve_aliases=True,
            submodules=True,
            docstring_parser="google",
        )
    except Exception as exc:  # noqa: BLE001 - report honestly, don't crash the job
        conn.execute(
            "UPDATE libraries SET status='error', note=? WHERE id=?",
            (f"griffe could not load {import_name!r}: {exc}"[:500], lib_id),
        )
        conn.commit()
        raise IndexError_(f"could not index {import_name}: {exc}") from exc

    conn.execute("DELETE FROM entries WHERE library_id=?", (lib_id,))

    budget = _Budget(MAX_ENTRIES_PER_LIBRARY)
    rows: list[tuple] = []

    root_summary, _notes, _r = _docstring_param_notes(root)
    root_dep, root_dep_note = _deprecation_of(root)
    root_doc = getattr(root, "docstring", None)
    root_full = (root_doc.value if root_doc else None) or None
    if root_full and len(root_full) > DOC_CAP:
        root_full = root_full[:DOC_CAP] + "\n... (truncated)"
    rows.append(
        (
            f"{lib_id}:{import_name}",
            lib_id,
            import_name,
            import_name,
            "module",
            f"module {import_name}",
            None,
            None,
            root_summary,
            root_full,
            int(root_dep),
            root_dep_note,
            str(getattr(root, "filepath", "") or "") or None,
            None,
            None,
        )
    )

    _walk(root, import_name, root, 1, budget, frozenset({id(root)}), rows, lib_id)

    conn.executemany(
        """
        INSERT OR REPLACE INTO entries
        (id, library_id, name, qualname, kind, signature, params_json, returns,
         summary, doc, deprecated, deprecated_note, source_path, source_line, parent_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    status = "partial" if budget.truncated else "done"
    note = f"indexed {len(rows)} entries" + (" (capped, package is larger)" if budget.truncated else "")
    conn.execute(
        "UPDATE libraries SET status=?, entry_count=?, note=?, indexed_at=? WHERE id=?",
        (status, len(rows), note, db.now(), lib_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
    return db.dump(row)


def index_python_import(
    conn, *, env: dict[str, Any], probe: dict[str, Any], import_name: str, force: bool = False
) -> dict[str, Any]:
    """Index ``import_name`` whether it is a distributed package or a
    stdlib module - the one entry point the API/checker/lazy-lookup paths
    should all share so the same import always resolves to the same
    library naming convention."""
    top = import_name.split(".")[0]
    if find_distribution(probe, top) is not None:
        return index_python_library(conn, env=env, probe=probe, import_name=top, force=force)
    return index_stdlib_module(conn, env=env, probe=probe, module_name=top, force=force)


def index_stdlib_module(conn, *, env: dict[str, Any], probe: dict[str, Any], module_name: str, force: bool = False) -> dict[str, Any]:
    stdlib = probe.get("stdlib")
    if not stdlib:
        raise IndexError_("probe did not report a stdlib path")
    top = module_name.split(".")[0]
    return index_python_library(
        conn,
        env=env,
        probe=probe,
        import_name=top,
        dist_name=f"stdlib/{top}",
        version=probe.get("python_version", "0"),
        search_paths=[stdlib],
        force=force,
        source=f"stdlib:{env['id']}",
    )
