"""``api_check_code``: find hallucinated / removed / misused APIs in a code
snippet by resolving names against the entries actually indexed for an
environment. Static only (``ast``), never executes the snippet.

Philosophy: zero false positives beats coverage. Every branch that cannot be
resolved with confidence (dynamic attribute access, unresolved base class,
untyped value, star-imports) is counted in ``unchecked`` and left silent.
"""
from __future__ import annotations

import ast
import difflib
from dataclasses import dataclass, field
from typing import Any

from . import db, indexing


@dataclass
class Finding:
    line: int
    col: int
    severity: str  # error | warning
    code: str
    symbol: str
    message: str
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = {
            "line": self.line,
            "col": self.col,
            "severity": self.severity,
            "code": self.code,
            "symbol": self.symbol,
            "message": self.message,
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


@dataclass
class _Ctx:
    conn: Any
    env: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0
    unchecked: int = 0
    libraries: set[str] = field(default_factory=set)
    _lib_cache: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    _module_children_cache: dict[str, list[str]] = field(default_factory=dict)


def _entry(conn, env_id: str, qualname: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT e.*, l.name AS lib_name, l.version AS lib_version FROM entries e
        JOIN libraries l ON l.id = e.library_id
        WHERE e.qualname=? AND l.env_id=? AND l.ecosystem='python'
        """,
        (qualname, env_id),
    ).fetchone()
    return dict(row) if row else None


def _children_names(conn, env_id: str, parent_qualname: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT e.qualname FROM entries e JOIN libraries l ON l.id=e.library_id
        WHERE l.env_id=? AND l.ecosystem='python' AND e.qualname LIKE ?
        """,
        (env_id, parent_qualname + ".%"),
    ).fetchall()
    out = []
    prefix_len = len(parent_qualname) + 1
    for r in rows:
        rest = r["qualname"][prefix_len:]
        if "." not in rest:
            out.append(rest)
    return out


def _ensure_indexed(ctx: _Ctx, top_name: str) -> bool:
    """Best-effort lazy index of a top-level import. Returns True if the
    library is now available (already indexed or newly indexed)."""
    env = ctx.env
    row = ctx.conn.execute(
        """
        SELECT l.* FROM libraries l WHERE l.env_id=? AND l.ecosystem='python'
          AND (l.name=? OR l.name=?)
        """,
        (env["id"], top_name, f"stdlib/{top_name}"),
    ).fetchone()
    if row:
        return row["status"] in ("done", "partial")
    if not env.get("python_path"):
        return False
    try:
        from . import environments

        probe = environments.probe_python(__import__("pathlib").Path(env["python_path"]))
    except Exception:
        return False
    try:
        indexing.index_python_import(ctx.conn, env=env, probe=probe, import_name=top_name)
        return True
    except Exception:
        return False


def _resolve_dotted(ctx: _Ctx, dotted: str) -> tuple[str | None, bool]:
    """Try to resolve a dotted import path to an indexed qualname.
    Returns (resolved_qualname_or_none, top_level_library_available)."""
    top = dotted.split(".")[0]
    available = _ensure_indexed(ctx, top)
    if not available:
        return None, False
    entry = _entry(ctx.conn, ctx.env["id"], dotted)
    if entry:
        return dotted, True
    return None, True


class _Checker(ast.NodeVisitor):
    def __init__(self, ctx: _Ctx):
        self.ctx = ctx
        # name -> {"qualname": <module or class path>, "role": "module"|"instance"|"class"}
        self.bindings: dict[str, dict[str, str]] = {}

    def _copy_scope(self) -> dict[str, dict[str, str]]:
        return dict(self.bindings)

    # -- imports -----------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            dotted = alias.name
            bind_name = alias.asname or dotted.split(".")[0]
            resolved, available = _resolve_dotted(self.ctx, dotted)
            if not available:
                self.ctx.unchecked += 1
                continue
            self.ctx.checked += 1
            if resolved is None:
                top = dotted.split(".")[0]
                siblings = _children_names(self.ctx.conn, self.ctx.env["id"], top)
                suggestion = difflib.get_close_matches(dotted.split(".")[-1], siblings, n=1)
                self.ctx.findings.append(
                    Finding(
                        node.lineno,
                        node.col_offset,
                        "error",
                        "unknown_module",
                        dotted,
                        f"'{dotted}' does not exist in the packages installed in this environment.",
                        suggestion[0] if suggestion else None,
                    )
                )
            else:
                self.bindings[bind_name] = {"qualname": resolved if alias.asname else dotted.split(".")[0], "role": "module"}
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            return  # relative imports ignored, per spec
        module = node.module or ""
        if not module:
            return
        top = module.split(".")[0]
        available = _ensure_indexed(self.ctx, top)
        if not available:
            self.ctx.unchecked += len(node.names)
            return
        module_entry = _entry(self.ctx.conn, self.ctx.env["id"], module)
        module_ok = module_entry is not None or module == top
        if not module_ok:
            self.ctx.checked += 1
            siblings = _children_names(self.ctx.conn, self.ctx.env["id"], top)
            suggestion = difflib.get_close_matches(module.split(".")[-1], siblings, n=1)
            self.ctx.findings.append(
                Finding(
                    node.lineno, node.col_offset, "error", "unknown_module", module,
                    f"'{module}' does not exist in the packages installed in this environment.",
                    suggestion[0] if suggestion else None,
                )
            )
            self.ctx.unchecked += len(node.names)
            return
        for alias in node.names:
            if alias.name == "*":
                self.ctx.unchecked += 1
                continue
            full = f"{module}.{alias.name}"
            entry = _entry(self.ctx.conn, self.ctx.env["id"], full)
            self.ctx.checked += 1
            bind_name = alias.asname or alias.name
            if entry is None:
                siblings = _children_names(self.ctx.conn, self.ctx.env["id"], module)
                suggestion = difflib.get_close_matches(alias.name, siblings, n=1)
                self.ctx.findings.append(
                    Finding(
                        node.lineno, node.col_offset, "error", "unknown_attribute", full,
                        f"'{alias.name}' does not exist in '{module}'.",
                        suggestion[0] if suggestion else None,
                    )
                )
            else:
                role = "class" if entry["kind"] == "class" else "module" if entry["kind"] == "module" else "value"
                self.bindings[bind_name] = {"qualname": full, "role": role}
        self.generic_visit(node)

    # -- assignments: infer instance types ---------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            return
        target = node.targets[0].id
        inferred = self._infer_call_result(node.value)
        if inferred:
            self.bindings[target] = inferred
        elif target in self.bindings:
            del self.bindings[target]

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                inferred = self._infer_call_result(item.context_expr)
                if inferred:
                    self.bindings[item.optional_vars.id] = inferred
        self.generic_visit(node)

    def _infer_call_result(self, expr: ast.expr) -> dict[str, str] | None:
        if not isinstance(expr, ast.Call):
            return None
        callee = self._dotted_of(expr.func)
        if callee is None:
            return None
        entry = self._resolve_binding_path(callee)
        if entry is None:
            return None
        if entry["kind"] == "class":
            return {"qualname": entry["qualname"], "role": "instance"}
        if entry["kind"] in ("function", "method") and entry.get("returns"):
            ret = (entry["returns"] or "").strip().split("[")[0].split(".")[-1]
            # best effort: only trust it if the return annotation names a
            # class we have indexed under the same library.
            candidates = self.ctx.conn.execute(
                "SELECT qualname FROM entries WHERE kind='class' AND name=? LIMIT 1",
                (ret,),
            ).fetchone()
            if candidates:
                return {"qualname": candidates["qualname"], "role": "instance"}
        return None

    # -- scoping: reset bindings copy per function/class body ---------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        outer = self.bindings
        self.bindings = self._copy_scope()
        self.generic_visit(node)
        self.bindings = outer

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        outer = self.bindings
        self.bindings = self._copy_scope()
        self.generic_visit(node)
        self.bindings = outer

    # -- attribute / call checking ------------------------------------
    def _dotted_of(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            base = self._dotted_of(node.value)
            if base is None:
                return None
            return f"{base}.{node.attr}"
        return None

    def _resolve_binding_path(self, dotted: str) -> dict[str, Any] | None:
        parts = dotted.split(".")
        root = parts[0]
        binding = self.bindings.get(root)
        if binding is None:
            return None
        qual = binding["qualname"]
        for part in parts[1:]:
            qual = f"{qual}.{part}"
        return _entry(self.ctx.conn, self.ctx.env["id"], qual)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        dotted_base = self._dotted_of(node.value)
        self.generic_visit(node)
        if dotted_base is None:
            return
        root = dotted_base.split(".")[0]
        binding = self.bindings.get(root)
        if binding is None:
            self.ctx.unchecked += 1
            return
        base_entry = None
        if dotted_base == root:
            base_qual = binding["qualname"]
        else:
            base_qual = self._resolve_binding_path(dotted_base) and self._resolve_binding_path(dotted_base)["qualname"]
        if base_qual is None:
            self.ctx.unchecked += 1
            return
        # Confirm base itself resolves to something real (module or class/instance).
        if binding["role"] != "module" or dotted_base != root:
            base_entry = _entry(self.ctx.conn, self.ctx.env["id"], base_qual)
            if base_entry is None:
                self.ctx.unchecked += 1
                return
        full = f"{base_qual}.{node.attr}"
        entry = _entry(self.ctx.conn, self.ctx.env["id"], full)
        self.ctx.checked += 1
        if entry is None:
            siblings = _children_names(self.ctx.conn, self.ctx.env["id"], base_qual)
            if not siblings:
                # We have no visibility into this namespace's members at all
                # (e.g. dynamic __getattr__) - stay silent rather than guess.
                self.ctx.unchecked += 1
                self.ctx.checked -= 1
                return
            suggestion = difflib.get_close_matches(node.attr, siblings, n=1)
            self.ctx.findings.append(
                Finding(
                    node.lineno, node.col_offset, "error", "unknown_attribute", full,
                    f"'{node.attr}' does not exist on '{base_qual}'.",
                    suggestion[0] if suggestion else None,
                )
            )
        elif entry["deprecated"]:
            self.ctx.findings.append(
                Finding(
                    node.lineno, node.col_offset, "warning", "deprecated", full,
                    entry["deprecated_note"] or f"'{full}' is deprecated.",
                )
            )

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        dotted = self._dotted_of(node.func)
        if dotted is None:
            return
        root = dotted.split(".")[0]
        if root not in self.bindings:
            return
        entry = self._resolve_binding_path(dotted)
        if entry is None:
            return  # already reported (or unchecked) by visit_Attribute/import handling
        if entry["kind"] not in ("function", "method", "class"):
            return
        params = db.from_json(entry.get("params_json")) or []
        # For a class call (constructor), params were only captured if we
        # parsed them from the __init__ docstring; without a resolvable
        # signature we cannot check arity/keywords, so stay silent.
        if not params and entry["kind"] == "class":
            self.ctx.unchecked += 1
            return
        has_star = any(p.get("kind") in ("VAR_POSITIONAL",) for p in params)
        has_kwstar = any(p.get("kind") in ("VAR_KEYWORD",) for p in params)
        has_arg_unpack = any(isinstance(a, ast.Starred) for a in node.args)
        has_kwarg_unpack = any(k.arg is None for k in node.keywords)
        param_names = {p["name"] for p in params if p.get("name") not in ("self", "cls")}
        positional_names = [
            p["name"] for p in params
            if p.get("name") not in ("self", "cls") and p.get("kind") in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")
        ]
        self.ctx.checked += 1
        # unexpected keyword
        if not has_kwstar:
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                if kw.arg not in param_names:
                    suggestion = difflib.get_close_matches(kw.arg, list(param_names), n=1)
                    self.ctx.findings.append(
                        Finding(
                            node.lineno, node.col_offset, "error", "unexpected_keyword", f"{dotted}({kw.arg}=...)",
                            f"'{kw.arg}' is not a parameter of '{dotted}'.",
                            suggestion[0] if suggestion else None,
                        )
                    )
        # too many positional
        if not has_star and not has_arg_unpack:
            n_pos = len(node.args)
            if n_pos > len(positional_names):
                self.ctx.findings.append(
                    Finding(
                        node.lineno, node.col_offset, "error", "too_many_positional", dotted,
                        f"'{dotted}' takes at most {len(positional_names)} positional argument(s), {n_pos} given.",
                    )
                )
        # missing required (conservative: no *args/**kwargs and no unpacking anywhere)
        if not has_star and not has_kwstar and not has_arg_unpack and not has_kwarg_unpack:
            required = [
                p["name"] for p in params
                if p.get("name") not in ("self", "cls")
                and p.get("kind") in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD", "KEYWORD_ONLY")
                and not p.get("default")
            ]
            supplied_kw = {kw.arg for kw in node.keywords if kw.arg}
            supplied_pos = positional_names[: len(node.args)]
            missing = [r for r in required if r not in supplied_kw and r not in supplied_pos]
            if missing:
                self.ctx.findings.append(
                    Finding(
                        node.lineno, node.col_offset, "error", "missing_required", dotted,
                        f"'{dotted}' is missing required argument(s): {', '.join(missing)}.",
                    )
                )
        if entry.get("deprecated"):
            self.ctx.findings.append(
                Finding(
                    node.lineno, node.col_offset, "warning", "deprecated", dotted,
                    entry.get("deprecated_note") or f"'{dotted}' is deprecated.",
                )
            )


def api_check_code(conn, code: str, *, env_row: dict[str, Any], language: str = "python") -> dict[str, Any]:
    if language == "typescript":
        return _check_typescript(conn, code, env_row)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return {
            "ok": False,
            "findings": [
                {
                    "line": exc.lineno or 1,
                    "col": exc.offset or 0,
                    "severity": "error",
                    "code": "syntax_error",
                    "symbol": "",
                    "message": str(exc.msg),
                }
            ],
            "checked": 0,
            "unchecked": 0,
            "env": env_row["id"],
            "libraries": [],
        }
    ctx = _Ctx(conn=conn, env=env_row)
    _Checker(ctx).visit(tree)
    libs = db.dump_all(
        conn.execute(
            "SELECT DISTINCT name, version FROM libraries WHERE env_id=? AND ecosystem='python'",
            (env_row["id"],),
        ).fetchall()
    )
    return {
        "ok": not any(f.severity == "error" for f in ctx.findings),
        "findings": [f.to_dict() for f in sorted(ctx.findings, key=lambda f: f.line)],
        "checked": ctx.checked,
        "unchecked": ctx.unchecked,
        "env": env_row["id"],
        "libraries": [f"{l['name']}@{l['version']}" for l in libs],
    }


def _check_typescript(conn, code: str, env_row: dict[str, Any]) -> dict[str, Any]:
    """v1 boundary: only checks that named imports exist in the package's
    indexed exports. No call/attribute checking for TS yet."""
    findings: list[dict[str, Any]] = []
    checked = 0
    unchecked = 0
    import re

    pattern = re.compile(r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]")
    for m in pattern.finditer(code):
        names = [n.strip().split(" as ")[0].strip() for n in m.group(1).split(",") if n.strip()]
        pkg = m.group(2)
        lib = conn.execute(
            "SELECT * FROM libraries WHERE ecosystem='js' AND env_id=? AND name=?",
            (env_row["id"], pkg),
        ).fetchone()
        if not lib:
            unchecked += len(names)
            continue
        line = code[: m.start()].count("\n") + 1
        for name in names:
            checked += 1
            entry = conn.execute(
                "SELECT * FROM entries WHERE library_id=? AND name=?", (lib["id"], name)
            ).fetchone()
            if entry is None:
                siblings = [
                    r["name"]
                    for r in conn.execute(
                        "SELECT DISTINCT name FROM entries WHERE library_id=?", (lib["id"],)
                    ).fetchall()
                ]
                suggestion = difflib.get_close_matches(name, siblings, n=1)
                findings.append(
                    Finding(
                        line, 0, "error", "unknown_attribute", f"{pkg}.{name}",
                        f"'{name}' is not exported by '{pkg}'.",
                        suggestion[0] if suggestion else None,
                    ).to_dict()
                )
    return {
        "ok": not any(f["severity"] == "error" for f in findings),
        "findings": findings,
        "checked": checked,
        "unchecked": unchecked,
        "env": env_row["id"],
        "libraries": [],
    }
