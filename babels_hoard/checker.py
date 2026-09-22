"""``api_check_code``: find hallucinated / removed / misused APIs in a code
snippet by resolving names against what is actually indexed for an
environment. Static only (``ast``); the snippet is never executed.

Philosophy: **zero false positives beats coverage.** A finding is an
``error`` only when the namespace in question is statically complete (see
``indexing`` for the completeness metadata). When a class or module can
produce names dynamically but is otherwise fully known (``__getattr__``,
``setattr(self, name, ...)``) an unknown name is a ``warning``. Everything
else - unresolved values, dynamic namespaces, code guarded by
``try/except ImportError``/``hasattr``/version checks, private names - is
counted in ``unchecked`` and left silent.

Value tracking is deliberately simple and conservative: imports, simple
assignments, ``with ... as``, annotated parameters/variables, and the
return annotation of indexed functions. Any other store to a name forgets
what was known about it.
"""
from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from . import db
from .symbols import Missing, SymbolIndex, parse_meta

_GUARD_EXCEPTIONS = {
    "ImportError", "ModuleNotFoundError", "AttributeError", "TypeError", "NameError",
    "Exception", "BaseException", "LookupError",
}
_GUARD_TEST_RE = re.compile(r"hasattr|getattr|version|VERSION|TYPE_CHECKING|find_spec|importlib|sys\.platform|os\.name")


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
class LocalClass:
    """A class defined in the snippet itself (``class Item(BaseModel)``)
    whose bases are all indexed classes: names it does not define itself
    are looked up on the bases."""

    name: str
    bases: list[dict[str, Any]]
    own: set[str]


@dataclass
class Value:
    """What an expression evaluates to, as far as the index can tell."""

    kind: str                      # module | class | instance | callable | local_class | local_instance
    entry: dict[str, Any] | None   # the module/class entry, or the function entry (None for local kinds)
    receiver: str | None = None    # for callables: "instance" | "class" | None (plain function)
    owner: dict[str, Any] | None = None  # for methods: the class entry they were reached through
    local: LocalClass | None = None


@dataclass
class _Ctx:
    symbols: SymbolIndex
    findings: list[Finding] = field(default_factory=list)
    checked: int = 0
    unchecked: int = 0
    libraries: dict[str, str] = field(default_factory=dict)


def _lib_label(e: dict[str, Any]) -> str:
    name = e.get("lib_name") or "?"
    if name.startswith("stdlib/"):
        return f"Python {e.get('lib_version')} stdlib"
    return f"{name} {e.get('lib_version')}"


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    t = handler.type
    if t is None:
        return {"BaseException"}
    nodes = t.elts if isinstance(t, ast.Tuple) else [t]
    out = set()
    for n in nodes:
        d = _dotted(n)
        if d:
            out.add(d.rsplit(".", 1)[-1])
    return out


class _Checker(ast.NodeVisitor):
    def __init__(self, ctx: _Ctx, source: str):
        self.ctx = ctx
        self.sym = ctx.symbols
        self.source = source
        self.bindings: dict[str, Value] = {}
        self.guard = 0
        self._types: dict[int, Value | None] = {}

    # ------------------------------------------------------------ helpers
    def _note_lib(self, e: dict[str, Any]) -> None:
        if e.get("lib_name"):
            self.ctx.libraries[e["lib_name"]] = e.get("lib_version") or ""

    def _report(self, node: ast.AST, severity: str, code: str, symbol: str, message: str, suggestion: str | None = None) -> None:
        if self.guard:
            self.ctx.unchecked += 1
            return
        self.ctx.findings.append(
            Finding(getattr(node, "lineno", 1), getattr(node, "col_offset", 0), severity, code, symbol, message, suggestion)
        )

    def _forget(self, name: str) -> None:
        self.bindings.pop(name, None)

    def _value_of_entry(self, e: dict[str, Any], *, instance: bool = False, receiver: str | None = None,
                        owner: dict[str, Any] | None = None) -> Value | None:
        kind = e["kind"]
        if kind == "module":
            return Value("module", e)
        if kind == "class":
            return Value("instance" if instance else "class", e)
        if kind in ("function", "method"):
            return Value("callable", e, receiver=receiver, owner=owner)
        return None

    def _instance_of_annotation(self, ann: ast.AST | None) -> Value | None:
        if ann is None:
            return None
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return None
        if not isinstance(ann, (ast.Name, ast.Attribute)):
            return None
        v = self._type_of(ann)
        if v is not None and v.kind == "class":
            return Value("instance", v.entry)
        if v is not None and v.kind == "local_class":
            return Value("local_instance", None, local=v.local)
        return None

    _CERTAINTY_RANK = {"unknown": 0, "warning": 1, "error": 2}

    def _local_member(self, local: LocalClass, attr: str) -> dict[str, Any] | Missing | None:
        """A member of a snippet class: None when the class body defines it
        (or may), else the first base that has it, else a Missing as sure
        as the least sure base."""
        if attr in local.own:
            return None
        missing: Missing | None = None
        for base in local.bases:
            m = self.sym.child(base, attr)
            if not isinstance(m, Missing):
                return m
            if missing is None or self._CERTAINTY_RANK[m.certainty] < self._CERTAINTY_RANK[missing.certainty]:
                missing = m
        return missing

    # --------------------------------------------------- type evaluation
    def _type_of(self, node: ast.AST) -> Value | None:
        key = id(node)
        if key in self._types:
            return self._types[key]
        self._types[key] = None  # recursion guard
        result = self._eval(node)
        self._types[key] = result
        return result

    def _eval(self, node: ast.AST) -> Value | None:
        if isinstance(node, ast.Name):
            return self.bindings.get(node.id)
        if isinstance(node, ast.Attribute):
            base = self._type_of(node.value)
            if base is None or base.kind == "callable":
                return None
            if base.local is not None:
                member = self._local_member(base.local, node.attr)
                if member is None or isinstance(member, Missing):
                    return None
                receiver = "instance" if base.kind == "local_instance" else "class"
                owner = self.sym.entry(member["qualname"].rsplit(".", 1)[0])  # the base that has it
                return self._value_of_entry(member, receiver=receiver, owner=owner)
            member = self.sym.child(base.entry, node.attr)
            if isinstance(member, Missing):
                return None
            if base.kind == "instance":
                return self._value_of_entry(member, receiver="instance", owner=base.entry)
            if base.kind == "class":
                return self._value_of_entry(member, receiver="class", owner=base.entry)
            return self._value_of_entry(member)
        if isinstance(node, ast.Await):
            if isinstance(node.value, ast.Call):
                return self._call_result(node.value, awaited=True)
            return None
        if isinstance(node, ast.Call):
            return self._call_result(node, awaited=False)
        return None

    def _call_result(self, node: ast.Call, *, awaited: bool) -> Value | None:
        callee = self._type_of(node.func)
        if callee is None:
            return None
        if callee.kind == "local_class":
            return Value("local_instance", None, local=callee.local)
        if callee.kind == "class":
            meta = parse_meta(callee.entry)
            if meta.get("ctor"):
                return Value("instance", self.sym.entry(self.sym.home(callee.entry)) or callee.entry)
            return None
        if callee.kind != "callable":
            return None
        meta = parse_meta(callee.entry)
        if meta.get("as") and not awaited:
            return None  # a coroutine object, not the annotated result
        if meta.get("rself") and callee.owner is not None and callee.receiver == "instance":
            return Value("instance", callee.owner)
        ret = meta.get("ret")
        if ret:
            cls = self.sym.class_by_target(ret)
            if cls is not None:
                return Value("instance", cls)
        return None

    # ------------------------------------------------------------ imports
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            dotted = alias.name
            top = dotted.split(".")[0]
            bind = alias.asname or top
            lib = self.sym.library_for(top)
            if lib is None:
                self.ctx.unchecked += 1
                self._forget(bind)
                continue
            target = self.sym.resolve(dotted)
            self.ctx.checked += 1
            if isinstance(target, Missing):
                self._forget(bind)
                self._missing_module(node, dotted, target)
                continue
            if target is None:
                self._forget(bind)
                self.ctx.unchecked += 1
                self.ctx.checked -= 1
                continue
            self._note_lib(target)
            # `import a.b` binds `a`; `import a.b as c` binds whatever a.b is.
            bound = target if alias.asname else self.sym.entry(top)
            value = self._value_of_entry(bound) if bound is not None else None
            if value is None or value.kind not in ("module", "class"):
                self._forget(bind)
            else:
                self.bindings[bind] = value

    def _missing_module(self, node: ast.AST, dotted: str, miss: Missing) -> None:
        if miss.certainty == "unknown":
            self.ctx.unchecked += 1
            self.ctx.checked -= 1
            return
        parent = miss.parent
        self._note_lib(parent)
        suggestion = self.sym.suggest(parent, miss.name)
        self._report(
            node,
            miss.certainty,
            "unknown_module",
            dotted,
            f"'{dotted}' does not exist in {_lib_label(parent)} ('{miss.name}' is not a submodule or member of '{parent['qualname']}').",
            f"{parent['qualname']}.{suggestion}" if suggestion else None,
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            for alias in node.names:
                self._forget(alias.asname or alias.name)
            return  # relative imports are the project's own code
        module = node.module or ""
        top = module.split(".")[0]
        lib = self.sym.library_for(top) if top else None
        if lib is None:
            self.ctx.unchecked += len(node.names)
            for alias in node.names:
                if alias.name == "*":
                    self.bindings.clear()
                else:
                    self._forget(alias.asname or alias.name)
            return
        mod = self.sym.resolve(module)
        if isinstance(mod, Missing) or mod is None or mod["kind"] not in ("module", "class"):
            for alias in node.names:
                self._forget(alias.asname or alias.name)
            if isinstance(mod, Missing):
                self.ctx.checked += 1
                self._missing_module(node, module, mod)
            else:
                self.ctx.unchecked += 1
            return
        self._note_lib(mod)
        for alias in node.names:
            if alias.name == "*":
                self.ctx.unchecked += 1
                self.bindings.clear()
                continue
            bind = alias.asname or alias.name
            member = self.sym.child(mod, alias.name)
            if isinstance(member, Missing):
                self._forget(bind)
                if member.certainty == "unknown":
                    self.ctx.unchecked += 1
                    continue
                self.ctx.checked += 1
                suggestion = self.sym.suggest(mod, alias.name)
                note = "" if member.certainty == "error" else " (the module resolves some names dynamically, so this is not certain)"
                self._report(
                    node, member.certainty, "unknown_attribute", f"{module}.{alias.name}",
                    f"'{alias.name}' cannot be imported from '{module}' in {_lib_label(mod)}{note}.",
                    suggestion,
                )
                continue
            self.ctx.checked += 1
            if member.get("deprecated"):
                self._report(node, "warning", "deprecated", f"{module}.{alias.name}",
                             member.get("deprecated_note") or f"'{module}.{alias.name}' is deprecated.")
            value = self._value_of_entry(member)
            if value is None:
                self._forget(bind)
            else:
                self.bindings[bind] = value

    # ---------------------------------------------------------- bindings
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._forget(node.id)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        value = self._type_of(node.value) if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) else None
        for t in node.targets:
            self.visit(t)
        if value is not None and value.kind in ("instance", "local_instance"):
            self.bindings[node.targets[0].id] = value  # type: ignore[union-attr]
        elif value is not None and value.kind in ("module", "class", "local_class") and isinstance(node.targets[0], ast.Name):
            self.bindings[node.targets[0].id] = value

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.annotation)
        self.visit(node.target)
        if isinstance(node.target, ast.Name):
            value = self._type_of(node.value) if node.value is not None else None
            if value is None or value.kind not in ("instance", "local_instance"):
                value = self._instance_of_annotation(node.annotation)
            if value is not None:
                self.bindings[node.target.id] = value

    def _yielded_by_context_manager(self, expr: ast.AST) -> Value | None:
        """``with client.stream(...) as r`` where ``stream`` is an
        ``@(async)contextmanager`` annotated ``-> (Async)Iterator[Response]``."""
        if not isinstance(expr, ast.Call):
            return None
        callee = self._type_of(expr.func)
        if callee is None or callee.kind != "callable":
            return None
        target = parse_meta(callee.entry).get("cm")
        cls = self.sym.class_by_target(target) if target else None
        return Value("instance", cls) if cls is not None else None

    def _with(self, node: ast.With | ast.AsyncWith, enter: str) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            bound = self._yielded_by_context_manager(item.context_expr)
            ctx_value = None if bound is not None else self._type_of(item.context_expr)
            if ctx_value is not None and ctx_value.kind == "instance":
                enter_e = self.sym.child(ctx_value.entry, enter)
                if not isinstance(enter_e, Missing):
                    meta = parse_meta(enter_e)
                    if meta.get("rself"):
                        bound = ctx_value
                    elif meta.get("ret"):
                        cls = self.sym.class_by_target(meta["ret"])
                        bound = Value("instance", cls) if cls else None
            if item.optional_vars is not None:
                self.visit(item.optional_vars)
                if bound is not None and isinstance(item.optional_vars, ast.Name):
                    self.bindings[item.optional_vars.id] = bound
        for stmt in node.body:
            self.visit(stmt)

    def visit_With(self, node: ast.With) -> None:
        self._with(node, "__enter__")

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._with(node, "__aenter__")

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._forget(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self._forget(node.name)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self._forget(node.name)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self._forget(node.rest)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        for n in node.names:
            self._forget(n)

    visit_Nonlocal = visit_Global  # type: ignore[assignment]

    # ------------------------------------------------------------ scopes
    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> None:
        for deco in getattr(node, "decorator_list", []):
            self.visit(deco)
        args = node.args
        for default in [*args.defaults, *[d for d in args.kw_defaults if d is not None]]:
            self.visit(default)
        outer = self.bindings
        self.bindings = dict(outer)
        all_args = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        for a in all_args:
            self._forget(a.arg)
            if a.annotation is not None:
                self.visit(a.annotation)
        for a in (args.vararg, args.kwarg):
            if a is not None:
                self._forget(a.arg)
        for a in all_args:
            value = self._instance_of_annotation(a.annotation)
            if value is not None:
                self.bindings[a.arg] = value
        if isinstance(node, ast.Lambda):
            self.visit(node.body)
        else:
            if node.returns is not None:
                self.visit(node.returns)
            for stmt in node.body:
                self.visit(stmt)
        self.bindings = outer
        if not isinstance(node, ast.Lambda):
            self._forget(node.name)

    visit_FunctionDef = _function  # type: ignore[assignment]
    visit_AsyncFunctionDef = _function  # type: ignore[assignment]
    visit_Lambda = _function  # type: ignore[assignment]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expr in [*node.decorator_list, *node.bases, *[k.value for k in node.keywords]]:
            self.visit(expr)
        local = self._local_class(node)
        outer = self.bindings
        self.bindings = dict(outer)
        for stmt in node.body:
            self.visit(stmt)
        self.bindings = outer
        self._forget(node.name)
        if local is not None:
            self.bindings[node.name] = Value("local_class", None, local=local)

    def _local_class(self, node: ast.ClassDef) -> LocalClass | None:
        """Track a snippet class only when nothing about it can add names
        behind our back: no metaclass/keywords, no decorators other than
        dataclass, every base an indexed class, and no dynamic attribute
        hooks in its body."""
        if node.keywords or not node.bases:
            return None
        for deco in node.decorator_list:
            target = deco.func if isinstance(deco, ast.Call) else deco
            if (_dotted(target) or "").rsplit(".", 1)[-1] != "dataclass":
                return None
        bases = []
        for b in node.bases:
            v = self._type_of(b)
            if v is None or v.kind != "class":
                return None
            bases.append(self.sym.entry(self.sym.home(v.entry)) or v.entry)
        own: set[str] = set()
        for stmt in node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                own.add(stmt.name)
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    own.add(sub.id)
                elif isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store) and isinstance(sub.value, ast.Name):
                    own.add(sub.attr)  # self.x = ..., cls.x = ...
                elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    own.add(sub.name)
                elif isinstance(sub, ast.Attribute) and sub.attr == "__dict__":
                    return None
                elif isinstance(sub, ast.Call) and _dotted(sub.func) in ("setattr", "object.__setattr__"):
                    return None
        if own & {"__getattr__", "__getattribute__", "__setattr__", "__slots__"}:
            return None
        return LocalClass(node.name, bases, own)

    def _comprehension(self, node: ast.AST) -> None:
        # Generators first: their targets shadow names used in the element.
        outer = self.bindings
        self.bindings = dict(outer)
        for gen in node.generators:  # type: ignore[attr-defined]
            self.visit(gen.iter)
            self.visit(gen.target)
            for cond in gen.ifs:
                self.visit(cond)
        for part in ("elt", "key", "value"):
            child = getattr(node, part, None)
            if child is not None:
                self.visit(child)
        self.bindings = outer

    visit_ListComp = _comprehension  # type: ignore[assignment]
    visit_SetComp = _comprehension  # type: ignore[assignment]
    visit_DictComp = _comprehension  # type: ignore[assignment]
    visit_GeneratorExp = _comprehension  # type: ignore[assignment]

    # ------------------------------------------------------------ guards
    def _guarded(self, stmts: list[ast.stmt]) -> None:
        self.guard += 1
        try:
            for s in stmts:
                self.visit(s)
        finally:
            self.guard -= 1

    def visit_Try(self, node: ast.Try) -> None:
        catches = set()
        for h in node.handlers:
            catches |= _handler_names(h)
        if catches & _GUARD_EXCEPTIONS:
            self._guarded(node.body)
        else:
            for s in node.body:
                self.visit(s)
        for h in node.handlers:
            self.visit(h)
        for s in [*node.orelse, *node.finalbody]:
            self.visit(s)

    visit_TryStar = visit_Try  # type: ignore[assignment]

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        test_src = ast.get_source_segment(self.source, node.test) or ""
        if _GUARD_TEST_RE.search(test_src):
            self._guarded(node.body)
            self._guarded(node.orelse)
        else:
            for s in [*node.body, *node.orelse]:
                self.visit(s)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.visit(node.test)
        test_src = ast.get_source_segment(self.source, node.test) or ""
        if _GUARD_TEST_RE.search(test_src):
            self.guard += 1
            try:
                self.visit(node.body)
                self.visit(node.orelse)
            finally:
                self.guard -= 1
        else:
            self.visit(node.body)
            self.visit(node.orelse)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # `hasattr(m, "x") and m.x()` - everything after a guard is guarded.
        guarded = False
        for v in node.values:
            if guarded:
                self.guard += 1
                try:
                    self.visit(v)
                finally:
                    self.guard -= 1
            else:
                self.visit(v)
                if _GUARD_TEST_RE.search(ast.get_source_segment(self.source, v) or ""):
                    guarded = True

    # ---------------------------------------------------------- attributes
    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.visit(node.value)
        if not isinstance(node.ctx, ast.Load):
            return  # assigning/deleting an attribute creates it; nothing to check
        base = self._type_of(node.value)
        if base is None:
            if isinstance(node.value, (ast.Name, ast.Attribute, ast.Call)):
                self.ctx.unchecked += 1
            return
        if base.kind == "callable":
            self.ctx.unchecked += 1
            return
        if base.local is not None:
            self._local_attribute(node, base)
            return
        member = self.sym.child(base.entry, node.attr)
        if not isinstance(member, Missing):
            self.ctx.checked += 1
            self._note_lib(member)
            if member.get("deprecated"):
                self._report(node, "warning", "deprecated", member["qualname"],
                             member.get("deprecated_note") or f"'{member['qualname']}' is deprecated.")
            return
        if member.certainty == "unknown":
            self.ctx.unchecked += 1
            return
        self.ctx.checked += 1
        owner = self.sym.entry(self.sym.home(base.entry)) or base.entry
        self._note_lib(owner)
        what = "an instance of " if base.kind == "instance" else ""
        suggestion = self.sym.suggest(base.entry, node.attr)
        if member.certainty == "error":
            message = f"'{node.attr}' does not exist on {what}'{owner['qualname']}' in {_lib_label(owner)}."
        else:
            message = (
                f"'{node.attr}' is not a declared member of {what}'{owner['qualname']}' in {_lib_label(owner)}; "
                "it only works if the class/module creates it dynamically (e.g. __getattr__)."
            )
        self._report(node, member.certainty, "unknown_attribute", f"{owner['qualname']}.{node.attr}", message, suggestion)

    def _local_attribute(self, node: ast.Attribute, base: Value) -> None:
        local = base.local
        assert local is not None
        member = self._local_member(local, node.attr)
        if member is None or (isinstance(member, Missing) and member.certainty == "unknown"):
            self.ctx.unchecked += 1
            return
        self.ctx.checked += 1
        if not isinstance(member, Missing):
            self._note_lib(member)
            if member.get("deprecated"):
                self._report(node, "warning", "deprecated", member["qualname"],
                             member.get("deprecated_note") or f"'{member['qualname']}' is deprecated.")
            return
        owner = self.sym.entry(self.sym.home(member.parent)) or member.parent
        self._note_lib(owner)
        what = "an instance of " if base.kind == "local_instance" else ""
        suggestion = None
        for b in local.bases:
            suggestion = suggestion or self.sym.suggest(b, node.attr)
        if member.certainty == "error":
            message = (
                f"'{node.attr}' does not exist on {what}'{local.name}': the class does not define it and "
                f"its base '{owner['qualname']}' in {_lib_label(owner)} has no such member."
            )
        else:
            message = (
                f"'{node.attr}' is not defined by '{local.name}' nor declared by its base '{owner['qualname']}' "
                f"in {_lib_label(owner)}; it only works if the base creates it dynamically (e.g. __getattr__)."
            )
        self._report(node, member.certainty, "unknown_attribute", f"{local.name}.{node.attr}", message, suggestion)

    # --------------------------------------------------------------- calls
    def visit_Call(self, node: ast.Call) -> None:
        func_name = node.func.id if isinstance(node.func, ast.Name) else None
        if func_name in ("isinstance", "issubclass", "type") and node.args and isinstance(node.args[0], ast.Name):
            # Narrowing: after `isinstance(x, Sub)` (in an if, an early return
            # or an assert) x may be a subclass with more attributes.
            self._forget(node.args[0].id)
        self.generic_visit(node)
        callee = self._type_of(node.func)
        if callee is None:
            return
        if callee.local is not None:
            self.ctx.unchecked += 1  # a snippet class's own constructor / __call__
            return
        label = _dotted(node.func) or (callee.entry["qualname"])
        if callee.kind == "class":
            meta = parse_meta(callee.entry)
            init = self.sym.child(callee.entry, "__init__")
            if not meta.get("ctor") or isinstance(init, Missing):
                self.ctx.unchecked += 1
                return
            self._check_arguments(node, init, drop_first=True, label=label)
            return
        if callee.kind == "instance":
            call = self.sym.child(callee.entry, "__call__")
            if isinstance(call, Missing):
                self.ctx.unchecked += 1
                return
            self._check_arguments(node, call, drop_first=True, label=label)
            return
        if callee.kind != "callable":
            return
        func = callee.entry
        mt = parse_meta(func).get("mt")
        if mt is None or mt == "s":
            drop = False
        elif mt == "c":
            drop = True
        else:  # instance method
            drop = callee.receiver == "instance"
        self._check_arguments(node, func, drop_first=drop, label=label)
        if func.get("deprecated"):
            self._report(node, "warning", "deprecated", func["qualname"],
                         func.get("deprecated_note") or f"'{func['qualname']}' is deprecated.")

    def _check_arguments(self, node: ast.Call, func: dict[str, Any], *, drop_first: bool, label: str) -> None:
        meta = parse_meta(func)
        if meta.get("sig0"):
            self.ctx.unchecked += 1
            return
        params = db.from_json(func.get("params_json")) or []
        if drop_first and params and params[0].get("kind") in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD"):
            params = params[1:]
        self._note_lib(func)
        where = f"{func['qualname']} ({_lib_label(func)})"
        self.ctx.checked += 1
        has_kwargs = any(p.get("kind") == "VAR_KEYWORD" for p in params)
        has_varargs = any(p.get("kind") == "VAR_POSITIONAL" for p in params)
        call_star = any(isinstance(a, ast.Starred) for a in node.args)
        call_kwstar = any(k.arg is None for k in node.keywords)
        overload_names = meta.get("ovn")
        if overload_names is not None:
            # Overloaded: only the keyword check is safe (a keyword no overload
            # that fits this call accepts).
            shapes = meta.get("ovp")
            if shapes and not call_star and not call_kwstar:
                fitting = [sh for sh in shapes if _overload_fits(sh, node, drop_first)]
                if not fitting or any(k == "w" for sh in fitting for _n, k, _r in sh):
                    return
                accepted = {n for sh in fitting for n, k, _r in sh if k in ("k", "o")} - {"self", "cls"}
            else:
                if meta.get("ovk"):
                    return
                accepted = set(overload_names) - {"self", "cls"}
            for kw in node.keywords:
                if kw.arg and kw.arg not in accepted:
                    self._unexpected_kw(node, kw.arg, label, where, sorted(accepted))
            return
        names = {p["name"] for p in params if p.get("kind") in ("POSITIONAL_OR_KEYWORD", "KEYWORD_ONLY")}
        positional_only = {p["name"] for p in params if p.get("kind") == "POSITIONAL_ONLY"}
        if not has_kwargs:
            for kw in node.keywords:
                if kw.arg is None:
                    continue
                if kw.arg in positional_only and kw.arg not in names:
                    self._report(
                        node, "error", "unexpected_keyword", f"{label}({kw.arg}=...)",
                        f"'{kw.arg}' is a positional-only parameter of {where}; pass it by position.",
                    )
                elif kw.arg not in names:
                    self._unexpected_kw(node, kw.arg, label, where, sorted(names))
        positional = [p for p in params if p.get("kind") in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")]
        if not has_varargs and not call_star and len(node.args) > len(positional):
            self._report(
                node, "error", "too_many_positional", label,
                f"{where} takes at most {len(positional)} positional argument(s), {len(node.args)} given.",
            )
        if not call_star and not call_kwstar:
            supplied_pos = {p["name"] for p in positional[: len(node.args)]}
            supplied_kw = {k.arg for k in node.keywords if k.arg}
            missing = [
                p["name"] for p in params
                if p.get("kind") in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD", "KEYWORD_ONLY")
                and p.get("default") is None
                and p["name"] not in supplied_pos
                and p["name"] not in supplied_kw
            ]
            if missing:
                self._report(
                    node, "error", "missing_required", label,
                    f"{where} is missing required argument(s): {', '.join(missing)}.",
                )

    def _unexpected_kw(self, node: ast.Call, kw: str, label: str, where: str, accepted: list[str]) -> None:
        self._report(
            node, "error", "unexpected_keyword", f"{label}({kw}=...)",
            f"'{kw}' is not a parameter of {where}.",
            _suggest_keyword(kw, accepted, label),
        )


def _overload_fits(shape: list[list[Any]], node: ast.Call, drop_first: bool) -> bool:
    """Could this overload (``[name, kind, required]`` per parameter) accept
    the call's positional arguments and required parameters? Keywords are
    not considered here: they are what gets checked against the fitting
    overloads."""
    params = list(shape)
    if drop_first and params and params[0][1] in ("p", "k"):
        params = params[1:]
    positional = [p for p in params if p[1] in ("p", "k")]
    n_args = len(node.args)
    if n_args > len(positional) and not any(p[1] == "v" for p in params):
        return False
    supplied = {k.arg for k in node.keywords if k.arg}
    for i, (name, kind, required) in enumerate(positional):
        if required and i >= n_args and (kind == "p" or name not in supplied):
            return False
    return all(name in supplied for name, kind, required in params if kind == "o" and required)


def _suggest_keyword(kw: str, accepted: list[str], label: str) -> str | None:
    """The parameter a mistaken keyword most likely means.

    A compound keyword whose parts are real parameters (``connect_timeout``
    for ``Timeout(connect=..., timeout=...)``) means the part that is not
    just the callable's own name; otherwise the closest spelling."""
    callee = label.rsplit(".", 1)[-1].lower()
    parts = [p for p in kw.lower().split("_") if p]
    matching = [a for a in accepted if a.lower() in parts]
    if len(matching) > 1:
        matching = [a for a in matching if a.lower() != callee] or matching
    if matching:
        return matching[0]
    close = difflib.get_close_matches(kw, accepted, n=1, cutoff=0.6)
    return close[0] if close else None


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
    ctx = _Ctx(symbols=SymbolIndex(conn, env_row))
    _Checker(ctx, code).visit(tree)
    findings = sorted(ctx.findings, key=lambda f: (f.line, f.col))
    # Same problem reported twice on one line (e.g. attribute + call) - keep one.
    unique: list[Finding] = []
    seen = set()
    for f in findings:
        key = (f.line, f.code, f.symbol)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return {
        "ok": not any(f.severity == "error" for f in unique),
        "findings": [f.to_dict() for f in unique[:25]],
        "truncated": len(unique) > 25,
        "checked": ctx.checked,
        "unchecked": ctx.unchecked,
        "env": env_row["id"],
        "libraries": [f"{n}@{v}" for n, v in sorted(ctx.libraries.items())],
    }


# ---------------------------------------------------------------- typescript
_TS_IMPORT_RE = re.compile(
    r"""(?:^|[;\n])\s*(?:import|export)\s+(type\s+)?(?:[A-Za-z_$][\w$]*\s*,\s*)?\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]""",
    re.M,
)


def _check_typescript(conn, code: str, env_row: dict[str, Any]) -> dict[str, Any]:
    """v1 boundary: only checks that named imports/re-exports
    (``import { A, type B } from "pkg"``) exist in the package's declared
    exports. No call/attribute checking for TS yet."""
    from . import node_indexing

    findings: list[dict[str, Any]] = []
    checked = 0
    unchecked = 0
    libraries: dict[str, str] = {}
    for m in _TS_IMPORT_RE.finditer(code):
        pkg = m.group(3)
        names = []
        for raw in m.group(2).split(","):
            item = re.sub(r"/\*.*?\*/|//[^\n]*", "", raw, flags=re.S).strip()
            if not item:
                continue
            item = re.sub(r"^type\s+", "", item)
            name = item.split(" as ")[0].strip()
            if name and name != "default":
                names.append(name)
        if pkg.startswith((".", "/")) or not names:
            unchecked += len(names)
            continue
        lib = node_indexing.current_js_library(conn, env_row, pkg)
        if lib is None or lib["status"] not in ("done", "partial"):
            unchecked += len(names)
            continue
        libraries[lib["name"]] = lib["version"]
        line = code[: m.start(2)].count("\n") + 1
        exported = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM entries WHERE library_id=? AND parent_id IS NULL", (lib["id"],)
            ).fetchall()
        }
        for name in names:
            if name in exported:
                checked += 1
                continue
            if lib["status"] == "partial":
                unchecked += 1  # export list was capped; absence proves nothing
                continue
            checked += 1
            elsewhere = conn.execute(
                "SELECT l.name FROM entries e JOIN libraries l ON l.id = e.library_id "
                "WHERE e.name = ? AND e.parent_id IS NULL AND l.ecosystem = 'js' AND l.env_id = ? "
                "AND l.id != ? AND l.status IN ('done', 'partial') LIMIT 1",
                (name, env_row["id"], lib["id"]),
            ).fetchone()
            if elsewhere is not None:
                # useFormStatus is react-dom's, not react's
                message = f"'{name}' is not exported by '{pkg}' {lib['version']}; it is exported by '{elsewhere['name']}'."
                suggestion_text = f'import {{ {name} }} from "{elsewhere["name"]}"'
            else:
                message = f"'{name}' is not exported by '{pkg}' {lib['version']}."
                close = difflib.get_close_matches(name, sorted(exported), n=1)
                suggestion_text = close[0] if close else None
            findings.append(
                Finding(line, 0, "error", "unknown_attribute", f"{pkg}.{name}", message, suggestion_text).to_dict()
            )
    return {
        "ok": not any(f["severity"] == "error" for f in findings),
        "findings": findings[:25],
        "truncated": len(findings) > 25,
        "checked": checked,
        "unchecked": unchecked,
        "env": env_row["id"],
        "libraries": [f"{n}@{v}" for n, v in sorted(libraries.items())],
    }
