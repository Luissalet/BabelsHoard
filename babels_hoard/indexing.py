"""Static Python indexing with griffe.

Babel never imports or executes the target project's code: griffe parses
source (AST) for pure-Python packages and ``.pyi`` stubs; compiled modules
without stubs are recorded honestly as such rather than invented.

What gets stored, and why
-------------------------
The walk is **breadth-first** from the package root, so the public surface
(``pandas.DataFrame``, ``pydantic.BaseModel``) is always indexed before deep
internals, even when a size cap cuts the walk short.

Every module/class object is *expanded* (its members recorded) exactly once,
at the first public path that reaches it - its **home**. Other public paths
to the same object (``pydantic.main.BaseModel`` for ``pydantic.BaseModel``)
are recorded as entries whose ``meta.see`` names the home, so lookups and
the checker can follow them without duplicating thousands of rows.

Each entry carries ``target`` (the canonical definition path griffe
resolved) and ``meta`` - the facts the code checker needs to stay silent
when it cannot be sure:

``dyn``    members may exist that static analysis cannot see. ``1``: the
           static member list is known to be incomplete (``globals()``
           tricks, star-imports from compiled modules, unresolved or builtin
           base classes, dynamic metaclasses) - the checker stays silent.
           ``"soft"``: complete except for a ``__getattr__`` or a
           ``setattr(self, <computed name>)`` that *could* supply other
           names - the checker reports unknown names as warnings only.
``nx``     the namespace was not expanded (depth/size cap, external module,
           unresolvable alias, compiled module).
``trunc``  the namespace's member list was cut by the size cap.
``see``    qualname of the home entry that holds this object's members.
``mt``     method type: ``i`` instance, ``c`` classmethod, ``s`` staticmethod.
``sig0``   the signature is not reliable for argument checks (a decorator
           that may change it).
``ovn``/``ovk``  overloaded function: union of parameter names / whether any
           overload takes ``**kwargs``.
``ret``    canonical path of the returned class (for instance inference);
``rself``  returns ``Self`` (or ``self``).
``ctor``   calling the class runs the indexed ``__init__`` (no ``__new__``,
           no dynamic metaclass, no signature-changing class decorator).
``as``     ``async def``: a call returns a coroutine.
"""
from __future__ import annotations

import ast
import hashlib
import logging
import re
import textwrap
import threading
from collections import deque
from pathlib import Path
from typing import Any

import griffe

from . import db

logger = logging.getLogger("babels_hoard.indexing")
logging.getLogger("griffe").setLevel(logging.ERROR)

MAX_ENTRIES_PER_LIBRARY = 15000
MAX_DEPTH = 8
MODULE_BUDGET = 1500          # modules parsed for the package itself
EXTERNAL_MODULE_BUDGET = 600  # modules parsed from other packages (bases, re-exports)
MAX_EXTERNAL_PACKAGES = 16    # other packages loaded to resolve bases / re-exports
DOC_CAP = 8000
SKIP_SUBMODULE_PARTS = {"tests", "test", "_tests", "conftest", "benchmarks", "benchmark"}
# Dunder members worth indexing: constructor/call/context-manager shapes.
KEEP_DUNDERS = {"__init__", "__call__", "__enter__", "__aenter__"}

INDEX_LOCK = threading.RLock()
"""Serialises index rebuilds (one library's DELETE+INSERT must not interleave
with another thread rebuilding the same library)."""

_KIND_MAP = {
    "positional-only": "POSITIONAL_ONLY",
    "positional or keyword": "POSITIONAL_OR_KEYWORD",
    "variadic positional": "VAR_POSITIONAL",
    "keyword-only": "KEYWORD_ONLY",
    "variadic keyword": "VAR_KEYWORD",
}

# Metaclasses known not to invent public attributes / not to change how
# attribute lookup works. Anything else makes a class "dynamic".
_STATIC_METACLASSES = {
    "abc.ABCMeta",
    "enum.EnumMeta",
    "enum.EnumType",
    "typing._ProtocolMeta",
    "typing_extensions._ProtocolMeta",
    "pydantic._internal._model_construction.ModelMetaclass",
}
# ... and of those, the ones whose __call__ still runs the class __init__.
_CTOR_METACLASSES = {
    "abc.ABCMeta",
    "typing._ProtocolMeta",
    "typing_extensions._ProtocolMeta",
    "pydantic._internal._model_construction.ModelMetaclass",
}
_IGNORABLE_BASES = {
    "object",
    "builtins.object",
    "typing.Generic",
    "typing.Protocol",
    "typing_extensions.Protocol",
    "abc.ABC",
}
_SAFE_CLASS_DECORATORS = {"dataclass", "final", "total_ordering", "runtime_checkable", "unique", "dataclass_transform"}
# Function decorators that do not change the accepted parameters.
_SAFE_DECORATOR_LAST = {
    "property", "staticmethod", "classmethod", "cached_property", "abstractmethod",
    "abstractproperty", "overload", "final", "override", "deprecated", "lru_cache",
    "cache", "contextmanager", "asynccontextmanager", "setter", "getter", "deleter",
    "wraps", "validate_call", "no_type_check", "runtime_checkable", "coroutine",
    "total_ordering", "singledispatchmethod", "register",
}
_SAFE_DECORATOR_RE = re.compile(r"(^|_)(doc|docs|appender|substitution|set_module|deprecate_nonkeyword_arguments|export|public)(_|$)", re.I)
_UNSAFE_DECORATOR_RE = re.compile(r"kwarg|rename|alias|positional|signature", re.I)
_SELF_TYPES = {"typing.Self", "typing_extensions.Self", "Self"}


class IndexError_(RuntimeError):
    pass


def library_id(ecosystem: str, name: str, version: str, source: str) -> str:
    raw = f"{ecosystem}:{name}:{version}:{source}"
    return "lib-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize_dist_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def find_distribution(probe: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Match an *import* name (e.g. ``yaml``) to the probed distribution that
    provides it (``PyYAML``).

    Distributions that actually ship the import (``top_level``) win; among
    several, the one named after the import. Meta-distributions that ship no
    modules of their own (griffe 2.x's ``griffe`` wraps ``griffelib``) are
    only used when nothing provides the name.
    """
    lname = name.lower()
    distributions = probe.get("distributions", [])
    providers = [d for d in distributions if lname in [t.lower() for t in d.get("top_level", [])]]
    for dist in providers:
        if normalize_dist_name(dist["name"]) == normalize_dist_name(lname):
            return dist
    if providers:
        return providers[0]
    for dist in distributions:
        if normalize_dist_name(dist["name"]) == normalize_dist_name(lname) and not dist.get("top_level"):
            return dist
    return None


def distribution_by_name(probe: dict[str, Any], dist_name: str) -> dict[str, Any] | None:
    want = normalize_dist_name(dist_name)
    for dist in probe.get("distributions", []):
        if normalize_dist_name(dist["name"]) == want:
            return dist
    return None


def import_names_for(probe: dict[str, Any], dist: dict[str, Any]) -> list[str]:
    names = [t for t in dist.get("top_level", []) if t and t.isidentifier() and not t.startswith("_")]
    if names:
        return names
    # Meta-distribution or missing metadata: fall back to the dist name.
    guess = dist["name"].replace("-", "_").lower()
    return [guess] if guess.isidentifier() else []


# ---------------------------------------------------------------- loader --
class _BudgetLoader(griffe.GriffeLoader):
    """A griffe loader that skips test suites and stops parsing new modules
    after a budget, so a giant package cannot take minutes by accident."""

    def __init__(self, *args: Any, budget: int, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.budget = budget
        self.loaded_modules = 0
        self.skipped_modules = 0
        self.external_modules = 0
        self.external_packages: list[str] = []
        self.failed_packages: set[str] = set()
        self._external = False

    def _load_submodules(self, module):  # griffe internal hook (pinned version)
        for subparts, subpath in self.finder.submodules(module):
            if any(part in SKIP_SUBMODULE_PARTS for part in subparts):
                continue
            if self._external:
                if self.external_modules >= EXTERNAL_MODULE_BUDGET:
                    continue
                self.external_modules += 1
            else:
                if self.loaded_modules >= self.budget:
                    self.skipped_modules += 1
                    continue
                self.loaded_modules += 1
            self._load_submodule(module, subparts, subpath)

    def ensure_package(self, name: str) -> bool:
        """Load another top-level package into the same collection (to
        resolve a base class or a re-export). Bounded and never raises."""
        if not name or name in self.modules_collection.members:
            return name in self.modules_collection.members
        if name in self.failed_packages or len(self.external_packages) >= MAX_EXTERNAL_PACKAGES:
            return False
        if self.external_modules >= EXTERNAL_MODULE_BUDGET:
            return False
        self._external = True
        try:
            self.load(name, submodules=True, try_relative_path=False, find_stubs_package=True)
        except Exception:  # noqa: BLE001 - ImportError, LoadingError, syntax errors in 3rd party code
            self.failed_packages.add(name)
            return False
        finally:
            self._external = False
        self.external_packages.append(name)
        return True


def _final(member: Any, loader: _BudgetLoader) -> Any:
    """Resolve an alias, loading the external package it points into once
    if needed. Raises when it cannot be resolved."""
    if not getattr(member, "is_alias", False):
        return member
    try:
        return member.final_target
    except Exception:
        target_path = getattr(member, "target_path", "") or ""
        top = target_path.split(".", 1)[0]
        if top and loader.ensure_package(top):
            return member.final_target
        raise


# ------------------------------------------------------------- analysis --
def _kind_of(obj: Any) -> str:
    return obj.kind.value if hasattr(obj.kind, "value") else str(obj.kind)


def _classify(obj: Any, parent: Any) -> str:
    labels = getattr(obj, "labels", set()) or set()
    kind = _kind_of(obj)
    if "property" in labels or "cached_property" in labels:
        return "property"
    if kind == "function":
        if parent is not None and _kind_of(parent) == "class":
            return "method"
        return "function"
    return kind


def _stringify(expr: Any) -> str | None:
    if expr is None:
        return None
    try:
        return str(expr)
    except Exception:
        return None


def _canonical(expr: Any) -> str | None:
    if expr is None:
        return None
    if isinstance(expr, str):
        return expr
    path = getattr(expr, "canonical_path", None)
    if isinstance(path, str):
        return path
    return _stringify(expr)


def _source_of(obj: Any) -> str:
    try:
        return obj.source or ""
    except Exception:
        return ""


_DOC_CACHE: dict[str, tuple[Any, tuple]] = {}
_NOT_SUMMARY = re.compile(r"^(!!!|\?\?\?|\.\. |[-=~^]{3,}$|:\w+:|\[!)")


def _summary_line(raw: str) -> str | None:
    """First meaningful line of a docstring: skips mkdocs admonitions
    (``!!! note "Usage"``), rst directives and underline rulers."""
    in_block = False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if in_block and raw_line[:1] in (" ", "\t"):
            continue  # body of an admonition / directive
        in_block = False
        if _NOT_SUMMARY.match(line):
            in_block = line.startswith(("!!!", "???", ".. "))
            continue
        return line[:300]
    return None


def _docstring_param_notes(obj: Any) -> tuple[str | None, dict[str, str], str | None]:
    """(summary, {param: description}, returns description) from the
    docstring, using griffe's google/numpy/sphinx style auto-detection."""
    doc = getattr(obj, "docstring", None)
    if doc is None or not doc.value:
        return None, {}, None
    raw = doc.value.strip()
    summary = _summary_line(raw)
    if _kind_of(obj) != "function" or ("\n" not in raw):
        return summary, {}, None
    key = getattr(obj, "path", None)
    cached = _DOC_CACHE.get(key) if key else None
    if cached is not None and cached[0] is doc:
        return cached[1]
    notes: dict[str, str] = {}
    returns_text = None
    try:
        sections = doc.parse("auto")
    except Exception:
        return summary, {}, None
    for section in sections:
        kind = section.kind.value if hasattr(section.kind, "value") else str(section.kind)
        if kind == "parameters":
            for p in section.value:
                d = (getattr(p, "description", "") or "").strip()
                if d:
                    notes[p.name] = d[:600]
        elif kind == "returns":
            parts = [
                (getattr(r, "description", "") or "").strip()
                for r in section.value
                if (getattr(r, "description", "") or "").strip()
            ]
            if parts:
                returns_text = " ".join(parts)[:600]
    result = (summary, notes, returns_text)
    if key:
        _DOC_CACHE[key] = (doc, result)
    return result


def _deprecation_of(obj: Any) -> tuple[bool, str | None]:
    dep = getattr(obj, "deprecated", None)
    if dep:
        return True, getattr(dep, "message", None) or (dep if isinstance(dep, str) else None) or "deprecated"
    return False, None


def _param_list(params: Any, notes: dict[str, str] | None = None) -> list[dict[str, Any]]:
    out = []
    for p in params or []:
        kind_raw = p.kind.value if hasattr(p.kind, "value") else str(p.kind)
        variadic = kind_raw in ("variadic positional", "variadic keyword")
        out.append(
            {
                "name": p.name,
                "kind": _KIND_MAP.get(kind_raw, kind_raw),
                "annotation": _stringify(p.annotation),
                "default": None if variadic else _stringify(p.default),
                "description": (notes or {}).get(p.name),
            }
        )
    return out


def _decorator_names(obj: Any) -> list[str]:
    names = []
    for d in getattr(obj, "decorators", None) or []:
        expr = d.value
        func = getattr(expr, "function", None)  # ExprCall -> the callee
        if func is not None:
            expr = func
        names.append(_canonical(expr) or "")
    return names


def _decorator_is_transparent(path: str) -> bool:
    last = path.rsplit(".", 1)[-1]
    if _UNSAFE_DECORATOR_RE.search(last):
        return False
    return last in _SAFE_DECORATOR_LAST or bool(_SAFE_DECORATOR_RE.search(last))


def _returns_self_by_source(func: Any) -> bool:
    """``def __enter__(self): ... return self`` without an annotation."""
    tree = _parse(_source_of(func))
    if tree is None:
        return False
    returns = [n for n in ast.walk(tree) if isinstance(n, ast.Return)]
    return bool(returns) and all(isinstance(r.value, ast.Name) and r.value.id == "self" for r in returns)


def _function_meta(func: Any, parent_kind: str | None) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    labels = getattr(func, "labels", set()) or set()
    if parent_kind == "class":
        meta["mt"] = "s" if "staticmethod" in labels else "c" if "classmethod" in labels else "i"
    if "async" in labels:
        meta["as"] = 1  # calling it returns a coroutine; the result type applies after await
    decorators = _decorator_names(func)
    if any(not _decorator_is_transparent(d) for d in decorators):
        meta["sig0"] = 1
    overloads = getattr(func, "overloads", None) or []
    if overloads:
        names: set[str] = set()
        takes_kwargs = False
        # The overloads are the documented contract; the implementation is
        # usually a catch-all ``(*args, **kwargs)``.
        for ov in overloads:
            for p in getattr(ov, "parameters", None) or []:
                kind_raw = p.kind.value if hasattr(p.kind, "value") else str(p.kind)
                if kind_raw == "variadic keyword":
                    takes_kwargs = True
                elif kind_raw != "variadic positional":
                    names.add(p.name)
        meta["ovn"] = sorted(names)
        if takes_kwargs:
            meta["ovk"] = 1
    ret = getattr(func, "returns", None)
    if ret is not None and type(ret).__name__ in ("ExprName", "ExprAttribute"):
        path = _canonical(ret)
        if path in _SELF_TYPES:
            meta["rself"] = 1
        elif path and not path.startswith(("builtins.", "typing.", "typing_extensions.", "collections.abc.")) and "." in path:
            meta["ret"] = path
        params = list(getattr(func, "parameters", None) or [])
        if params and parent_kind == "class" and _stringify(params[0].annotation) == _stringify(ret):
            meta["rself"] = 1  # def __enter__(self: T) -> T
    elif ret is None and func.name in ("__enter__", "__aenter__") and _returns_self_by_source(func):
        meta["rself"] = 1
    return meta


_RESTORE_METHODS = {"__setstate__", "__setattr__", "__copy__", "__deepcopy__", "__reduce__", "__reduce_ex__", "__getattr__"}


def _parse(src: str) -> ast.AST | None:
    if not src:
        return None
    try:
        return ast.parse(textwrap.dedent(src))
    except (SyntaxError, ValueError):
        return None


def _is_call_to(node: ast.AST, names: set[str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id in names) or (isinstance(f, ast.Attribute) and f.attr in names)


def _module_source_facts(src: str) -> tuple[bool, list[str]]:
    """(dynamic, star_import_sources) for a module's source.

    Dynamic: ``globals()``/``vars()`` used outside a module ``__getattr__``
    (lazy loaders cache into globals, that case is covered by the
    ``getattr`` flag), writes through ``sys.modules[...]``, or an
    ``@enum.global_enum`` class that injects its members as module names.
    """
    tree = _parse(src)
    if tree is None:
        return False, []
    stars: list[str] = []
    dynamic = False

    def visit(node: ast.AST, in_getattr: bool) -> None:
        nonlocal dynamic
        for child in ast.iter_child_nodes(node):
            if dynamic:
                return
            if isinstance(child, ast.ImportFrom) and any(a.name == "*" for a in child.names):
                stars.append("." * (child.level or 0) + (child.module or ""))
            elif isinstance(child, ast.Call) and _is_call_to(child, {"globals", "vars"}) and not in_getattr:
                if not child.args:
                    dynamic = True
            elif isinstance(child, ast.Subscript) and isinstance(child.value, ast.Attribute) and child.value.attr == "modules":
                if isinstance(child.value.value, ast.Name) and child.value.value.id == "sys" and not in_getattr:
                    dynamic = True
            elif isinstance(child, ast.ClassDef):
                for deco in child.decorator_list:
                    target = deco.func if isinstance(deco, ast.Call) else deco
                    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                    if name == "global_enum":
                        dynamic = True
            visit(child, in_getattr or (isinstance(child, ast.FunctionDef) and child.name == "__getattr__"))

    visit(tree, False)
    return dynamic, stars


_CLASS_SRC_CACHE: dict[str, bool] = {}


def _class_dynamic_cached(cls: Any) -> bool:
    key = getattr(cls, "path", None)
    if key in _CLASS_SRC_CACHE:
        return _CLASS_SRC_CACHE[key]
    value = _class_source_dynamic(_source_of(cls))
    if key:
        _CLASS_SRC_CACHE[key] = value
    return value


def _class_source_dynamic(src: str) -> bool:
    """True when a class body can create attributes by name at runtime:
    ``setattr(self, <expr>, ...)`` or ``self.__dict__.update/[...]=`` in a
    method other than the pickling/copy protocol ones."""
    tree = _parse(src)
    if tree is None:
        return False
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) or func.name in _RESTORE_METHODS:
            continue
        for node in ast.walk(func):
            if _is_call_to(node, {"setattr"}) and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "self":
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                    return True
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("update", "setdefault"):
                owner = node.func.value
                if isinstance(owner, ast.Attribute) and owner.attr == "__dict__":
                    return True
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute) and t.value.attr == "__dict__":
                        return True
    return False


def _resolve_relative(module_path: str, is_package: bool, spec: str) -> str:
    if not spec.startswith("."):
        return spec
    level = len(spec) - len(spec.lstrip("."))
    rest = spec[level:]
    base = module_path.split(".")
    if not is_package:
        base = base[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    return ".".join([*base, rest] if rest else base)


class _Analyzer:
    """Memoised completeness analysis of modules and classes."""

    def __init__(self, loader: _BudgetLoader):
        self.loader = loader
        self._module: dict[str, Any] = {}
        self._class: dict[str, dict[str, Any]] = {}

    # modules --------------------------------------------------------
    def module_dyn(self, mod: Any, _stack: tuple = ()) -> Any:
        path = mod.path
        if path in self._module:
            return self._module[path]
        if path in _stack:
            return 0
        self._module[path] = 0  # provisional (cycles)
        dyn: Any = 0
        members = getattr(mod, "members", {}) or {}
        if any("*" in name for name in members):  # griffe keeps unexpandable star-imports as "<mod>/*"
            dyn = 1
        dynamic_src, stars = _module_source_facts(_source_of(mod)) if not dyn else (False, [])
        if dynamic_src:
            dyn = 1
        if not dyn and stars:
            is_pkg = str(getattr(mod, "filepath", "")).endswith(("__init__.py", "__init__.pyi"))
            for spec in stars:
                source_mod = _resolve_relative(path, is_pkg, spec)
                try:
                    target = self.loader.modules_collection.get_member(source_mod)
                    target = target.final_target if getattr(target, "is_alias", False) else target
                except Exception:
                    dyn = 1  # star-import from something we cannot see (compiled / not loaded)
                    break
                if self.module_dyn(target, _stack + (path,)) == 1:
                    dyn = 1
                    break
        if not dyn and "__getattr__" in members:
            dyn = "soft"
        self._module[path] = dyn
        return dyn

    # classes --------------------------------------------------------
    def class_meta(self, cls: Any) -> dict[str, Any]:
        path = cls.path
        if path in self._class:
            return self._class[path]
        meta: dict[str, Any] = {}
        dyn = False
        getattr_only = False
        ctor = True
        # Make sure the packages holding the bases are loaded (bounded).
        for base in getattr(cls, "bases", None) or []:
            bpath = _canonical(base) or ""
            if bpath and bpath.split("[")[0] not in _IGNORABLE_BASES:
                self.loader.ensure_package(bpath.split(".", 1)[0])
        meaningful = [
            b for b in (getattr(cls, "bases", None) or [])
            if (_canonical(b) or "").split("[")[0] not in _IGNORABLE_BASES
        ]
        try:
            resolved = [b for b in cls.resolved_bases if getattr(b, "is_class", False)]
        except Exception:
            resolved = []
        if len(resolved) < len(meaningful):
            dyn = True
            ctor = False
        try:
            mro = cls.mro()
        except Exception:
            mro = []
            dyn = True
            ctor = False
        for c in [cls, *mro]:
            members = getattr(c, "members", {}) or {}
            if "__getattribute__" in members:
                dyn = True
            elif "__getattr__" in members:
                getattr_only = True
            if "__new__" in members:
                ctor = False
            keywords = getattr(c, "keywords", None) or {}
            if "metaclass" in keywords:
                mc = _canonical(keywords["metaclass"]) or ""
                if mc not in _STATIC_METACLASSES:
                    dyn = True
                if mc not in _CTOR_METACLASSES:
                    ctor = False
            for deco in _decorator_names(c):
                if deco.rsplit(".", 1)[-1] not in _SAFE_CLASS_DECORATORS:
                    dyn = True
                    ctor = False
            if _class_dynamic_cached(c):
                getattr_only = True  # setattr(self, <computed name>, ...) - may or may not add names
        if dyn:
            meta["dyn"] = 1
        elif getattr_only:
            meta["dyn"] = "soft"
        if ctor:
            meta["ctor"] = 1
        self._class[path] = meta
        return meta


def _extra_instance_attributes(cls: Any) -> list[str]:
    """Public ``self.<name> = ...`` assignments anywhere in the class body
    (griffe only records those made in ``__init__``)."""
    tree = _parse(_source_of(cls))
    if tree is None:
        return []
    names: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets = [node.target]
        for t in targets:
            for sub in ast.walk(t):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "self"
                    and not sub.attr.startswith("_")
                ):
                    names.add(sub.attr)
    return sorted(names)


# ------------------------------------------------------------------ walk --
_ROW_FIELDS = (
    "id", "library_id", "name", "qualname", "kind", "signature", "params_json", "returns",
    "summary", "doc", "deprecated", "deprecated_note", "source_path", "source_line",
    "parent_id", "target", "meta_json",
)
_INSERT_SQL = (
    "INSERT OR REPLACE INTO entries (" + ", ".join(_ROW_FIELDS) + ") VALUES ("
    + ",".join("?" for _ in _ROW_FIELDS) + ")"
)


def _cap_doc(text: str | None) -> str | None:
    if not text:
        return None
    return text[:DOC_CAP] + "\n... (truncated)" if len(text) > DOC_CAP else text


def _signature(name: str, obj: Any, kind: str) -> str:
    if kind in ("function", "method"):
        parts = []
        for p in getattr(obj, "parameters", None) or []:
            kind_raw = p.kind.value if hasattr(p.kind, "value") else str(p.kind)
            piece = ("*" if kind_raw == "variadic positional" else "**" if kind_raw == "variadic keyword" else "") + p.name
            if p.annotation is not None:
                piece += f": {_stringify(p.annotation)}"
            if p.default is not None:
                piece += f" = {_stringify(p.default)}"
            parts.append(piece)
        ret = _stringify(getattr(obj, "returns", None))
        prefix = "async " if "async" in (getattr(obj, "labels", set()) or set()) else ""
        return f"{prefix}{name}({', '.join(parts)})" + (f" -> {ret}" if ret else "")
    if kind == "class":
        bases = ", ".join(_stringify(b) or "" for b in (getattr(obj, "bases", None) or []))
        return f"class {name}({bases})" if bases else f"class {name}"
    if kind in ("attribute", "property"):
        ann = _stringify(getattr(obj, "annotation", None))
        if kind == "property" and ann is None:
            ann = _stringify(getattr(obj, "returns", None))
        val = _stringify(getattr(obj, "value", None)) if kind == "attribute" else None
        sig = name + (f": {ann}" if ann else "")
        if val and len(val) <= 120:
            sig += f" = {val}"
        return sig
    if kind == "module":
        return f"module {getattr(obj, 'path', name)}"
    return name


class _Walker:
    def __init__(self, lib_id: str, package_root: str, loader: _BudgetLoader, cap: int):
        self.lib_id = lib_id
        self.package_root = package_root
        self.loader = loader
        self.cap = cap
        self.analyzer = _Analyzer(loader)
        self.rows: dict[str, dict[str, Any]] = {}
        self.homes: dict[str, str] = {}
        self.truncated = False
        self._inherited_cache: dict[str, dict[str, Any]] = {}

    def _eid(self, qualname: str) -> str:
        return f"{self.lib_id}:{qualname}"

    def _add(self, qualname: str, name: str, kind: str, obj: Any | None, parent_q: str | None, meta: dict[str, Any], **fields: Any) -> dict[str, Any]:
        row = {
            "id": self._eid(qualname),
            "library_id": self.lib_id,
            "name": name,
            "qualname": qualname,
            "kind": kind,
            "signature": None,
            "params_json": None,
            "returns": None,
            "summary": None,
            "doc": None,
            "deprecated": 0,
            "deprecated_note": None,
            "source_path": None,
            "source_line": None,
            "parent_id": self._eid(parent_q) if parent_q else None,
            "target": getattr(obj, "path", None) if obj is not None else None,
            "meta": meta,
        }
        row.update(fields)
        self.rows[qualname] = row
        return row

    def _describe(self, name: str, obj: Any, kind: str, parent_kind: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        summary, notes, returns_text = _docstring_param_notes(obj)
        deprecated, dep_note = _deprecation_of(obj)
        meta: dict[str, Any] = {}
        params: list[dict[str, Any]] = []
        returns = None
        if kind in ("function", "method"):
            params = _param_list(getattr(obj, "parameters", None), notes)
            meta.update(_function_meta(obj, parent_kind))
            ann = _stringify(getattr(obj, "returns", None))
            returns = " - ".join(x for x in (ann, returns_text) if x) or None
        elif kind == "property":
            ann = _stringify(getattr(obj, "returns", None)) or _stringify(getattr(obj, "annotation", None))
            returns = " - ".join(x for x in (ann, returns_text) if x) or None
        filepath = getattr(obj, "filepath", None)
        if isinstance(filepath, list):
            filepath = filepath[0] if filepath else None
        fields = {
            "signature": _signature(name, obj, kind),
            "params_json": db.to_json(params) if params else None,
            "returns": returns,
            "summary": summary,
            "doc": _cap_doc(getattr(getattr(obj, "docstring", None), "value", None)),
            "deprecated": int(deprecated),
            "deprecated_note": dep_note,
            "source_path": str(filepath) if filepath else None,
            "source_line": getattr(obj, "lineno", None),
        }
        return fields, meta

    def _inherited(self, cls: Any) -> dict[str, Any]:
        key = cls.path
        if key not in self._inherited_cache:
            try:
                self._inherited_cache[key] = dict(cls.inherited_members)
            except Exception:
                self._inherited_cache[key] = {}
        return self._inherited_cache[key]

    def _members(self, obj: Any) -> list[tuple[str, Any]]:
        members = getattr(obj, "members", None) or {}
        items = list(members.items())
        if _kind_of(obj) == "class":
            inherited = self._inherited(obj)
            for name, member in (inherited or {}).items():
                if name not in members:
                    items.append((name, member))
        return items

    def _is_public(self, name: str, obj: Any, parent: Any) -> bool:
        if "*" in name:
            return False
        if not name.startswith("_"):
            return True
        if _kind_of(parent) == "class" and name in KEEP_DUNDERS:
            return True
        exports = getattr(parent, "exports", None)
        if exports:
            try:
                return name in {str(e) for e in exports}
            except Exception:
                return False
        return False

    def _helper_import(self, name: str, member: Any, parent: Any) -> bool:
        """An alias into another package that the module does not re-export
        (``__all__`` exists and omits it), e.g. ``from warnings import warn``.
        It exists as an attribute, but loading that package to describe it
        would waste the external budget."""
        if not getattr(member, "is_alias", False):
            return False
        target_top = (getattr(member, "target_path", "") or "").split(".", 1)[0]
        if not target_top or target_top == self.package_root or target_top in self.loader.modules_collection.members:
            return False
        exports = getattr(parent, "exports", None)
        if not exports:
            return False
        try:
            return name not in {str(e) for e in exports}
        except Exception:
            return False

    def run(self, root: Any, root_q: str) -> None:
        fields, meta = self._describe(root_q, root, "module", None)
        dyn = self.analyzer.module_dyn(root)
        if dyn:
            meta["dyn"] = dyn
        self._add(root_q, root_q.rsplit(".", 1)[-1], "module", root, None, meta, **fields)
        self.homes[root.path] = root_q
        queue: deque[tuple[Any, str, int]] = deque([(root, root_q, 1)])
        while queue:
            obj, qual, depth = queue.popleft()
            if len(self.rows) >= self.cap:
                self.rows[qual]["meta"]["nx"] = 1
                self.truncated = True
                for _o, q, _d in queue:
                    self.rows[q]["meta"]["nx"] = 1
                break
            self._expand(obj, qual, depth, queue)

    def _expand(self, obj: Any, qual: str, depth: int, queue: deque) -> None:
        parent_kind = _kind_of(obj)
        seen_names: set[str] = set()
        for name, member in self._members(obj):
            if len(self.rows) >= self.cap:
                self.rows[qual]["meta"]["trunc"] = 1
                self.truncated = True
                return
            if not self._is_public(name, member, obj):
                continue
            seen_names.add(name)
            child_q = f"{qual}.{name}"
            if child_q in self.rows:
                continue
            try:
                if self._helper_import(name, member, obj):
                    raise LookupError("imported helper, not a re-export")
                resolved = _final(member, self.loader)
            except Exception:
                # A real name griffe cannot resolve statically (a conditional
                # or cross-package alias like ``os.path``): record that it
                # exists, and that nothing below it is known.
                self._add(
                    child_q, name, "attribute", None, qual, {"nx": 1, "unres": 1},
                    signature=f"{name} (unresolved alias)",
                    summary="Babel could not statically resolve this name to a single target (a conditional or external alias); nothing further is known about it.",
                    target=getattr(member, "target_path", None),
                )
                continue
            if resolved is None:
                continue
            kind = _classify(resolved, obj)
            if kind not in ("module", "class", "function", "method", "attribute", "property"):
                continue
            fields, meta = self._describe(name, resolved, kind, parent_kind)
            if kind == "module":
                dyn = self.analyzer.module_dyn(resolved)
                if dyn:
                    meta["dyn"] = dyn
                if getattr(resolved, "filepath", None) is None:
                    meta["nx"] = 1
                    meta["compiled"] = 1
                    fields["summary"] = fields["summary"] or "compiled, no stubs"
            elif kind == "class":
                meta.update(self.analyzer.class_meta(resolved))
                init = None
                try:
                    init = (getattr(resolved, "members", {}) or {}).get("__init__") or self._inherited(resolved).get("__init__")
                    init = init.final_target if getattr(init, "is_alias", False) else init
                except Exception:
                    init = None
                if init is not None and _kind_of(init) == "function":
                    params = _param_list(getattr(init, "parameters", None), _docstring_param_notes(init)[1])
                    if params and params[0]["name"] in ("self", "cls"):
                        params = params[1:]
                    fields["params_json"] = db.to_json(params) if params else None
            row = self._add(child_q, name, kind, resolved, qual, meta, **fields)
            if kind in ("module", "class"):
                home = self.homes.get(resolved.path)
                external_module = kind == "module" and resolved.path.split(".", 1)[0] != self.package_root
                if home is not None:
                    row["meta"]["see"] = home
                elif external_module or meta.get("compiled"):
                    row["meta"]["nx"] = 1
                elif depth >= MAX_DEPTH:
                    row["meta"]["nx"] = 1
                else:
                    self.homes[resolved.path] = child_q
                    queue.append((resolved, child_q, depth + 1))
        if parent_kind == "class":
            for attr in _extra_instance_attributes(obj):
                if attr in seen_names or f"{qual}.{attr}" in self.rows:
                    continue
                self._add(
                    f"{qual}.{attr}", attr, "attribute", None, qual, {},
                    signature=attr, summary="Instance attribute (assigned outside __init__).",
                    target=f"{obj.path}.{attr}",
                )

    def tuples(self) -> list[tuple]:
        out = []
        for row in self.rows.values():
            meta = row.pop("meta")
            row["meta_json"] = db.to_json(meta) if meta else None
            out.append(tuple(row[f] for f in _ROW_FIELDS))
        return out


# ------------------------------------------------------------ entry points --
def _supersede_older(conn, lib_id: str, name: str, source: str) -> None:
    conn.execute(
        "UPDATE libraries SET status='superseded' WHERE ecosystem='python' AND name=? AND source=? "
        "AND id != ? AND status IN ('done', 'partial')",
        (name, source, lib_id),
    )


def _write_library(conn, lib_id: str, *, name: str, version: str, source: str, env_id: str,
                   rows: list[tuple], status: str, note: str) -> dict[str, Any]:
    """Replace one library's entries in a single short transaction."""
    conn.execute(
        """
        INSERT INTO libraries (id, ecosystem, name, version, source, env_id, status, created_at)
        VALUES (?, 'python', ?, ?, ?, ?, 'indexing', ?)
        ON CONFLICT(id) DO UPDATE SET status='indexing'
        """,
        (lib_id, name, version, source, env_id, db.now()),
    )
    conn.execute("DELETE FROM entries WHERE library_id=?", (lib_id,))
    conn.executemany(_INSERT_SQL, rows)
    conn.execute(
        "UPDATE libraries SET status=?, entry_count=?, note=?, indexed_at=? WHERE id=?",
        (status, len(rows), note, db.now(), lib_id),
    )
    _supersede_older(conn, lib_id, name, source)
    conn.commit()
    return db.dump(conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())


def _compiled_module_file(search_paths: list[str], name: str) -> Path | None:
    for sp in search_paths:
        base = Path(sp)
        if not base.is_dir():
            continue
        try:
            for child in base.iterdir():
                n = child.name
                if n.startswith(name + ".") and n.endswith((".so", ".pyd")):
                    return child
                if n == name and child.is_dir():
                    for sub in child.iterdir():
                        if sub.name.startswith("__init__.") and sub.name.endswith((".so", ".pyd")):
                            return sub
        except OSError:
            continue
    return None


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
    if dist_name is None:
        dist = find_distribution(probe, import_name)
        if dist is not None:
            dist_name = dist["name"]
            version = dist["version"]
    version = version or "0.0.0"
    dist_name = dist_name or import_name

    lib_id = library_id("python", dist_name, version, source)
    with INDEX_LOCK:
        existing = conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
        if existing and existing["status"] in ("done", "partial") and not force:
            return db.dump(existing)
        if existing and existing["status"] == "superseded" and not force:
            # The same version is back (downgrade/reinstall): reuse it.
            conn.execute("UPDATE libraries SET status=CASE WHEN note LIKE '%capped%' THEN 'partial' ELSE 'done' END WHERE id=?", (lib_id,))
            _supersede_older(conn, lib_id, dist_name, source)
            conn.commit()
            return db.dump(conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())

        paths = list(search_paths or probe.get("sys_path") or [])
        loader = _BudgetLoader(
            search_paths=paths,
            allow_inspection=False,
            docstring_parser="google",
            budget=MODULE_BUDGET,
        )
        try:
            root = loader.load(import_name, submodules=True, find_stubs_package=True)
            if getattr(root, "is_alias", False):
                root = root.final_target
        except Exception as exc:  # noqa: BLE001 - report honestly, don't crash the job
            compiled = _compiled_module_file(paths, import_name)
            if compiled is None and import_name in set(probe.get("builtin_modules") or []):
                compiled = "(built into the interpreter)"
            if compiled is None:
                raise IndexError_(f"could not index {import_name}: {exc}") from exc
            rows = [
                (
                    f"{lib_id}:{import_name}", lib_id, import_name, import_name, "module",
                    f"module {import_name}", None, None, "compiled, no stubs", None, 0, None,
                    str(compiled), None, None, import_name, db.to_json({"nx": 1, "compiled": 1}),
                )
            ]
            return _write_library(
                conn, lib_id, name=dist_name, version=version, source=source, env_id=env["id"], rows=rows,
                status="done", note="compiled extension without .pyi stubs: nothing can be read statically",
            )

        walker = _Walker(lib_id, import_name.split(".")[0], loader, MAX_ENTRIES_PER_LIBRARY)
        walker.run(root, import_name)
        if loader.skipped_modules:
            # Some submodules were never parsed, so no module's member list
            # can be trusted to be complete.
            for row in walker.rows.values():
                if row["kind"] == "module":
                    row["meta"]["trunc"] = 1
        rows = walker.tuples()
        partial = walker.truncated or loader.skipped_modules > 0
        note = f"indexed {len(rows)} entries"
        if walker.truncated:
            note += " (capped, package is larger)"
        if loader.skipped_modules:
            note += f"; {loader.skipped_modules} modules not parsed (module budget {MODULE_BUDGET})"
        if loader.external_packages:
            note += f"; bases/re-exports resolved from {', '.join(loader.external_packages[:5])}"
        return _write_library(
            conn, lib_id, name=dist_name, version=version, source=source, env_id=env["id"], rows=rows,
            status="partial" if partial else "done", note=note[:500],
        )


def _stdlib_has(probe: dict[str, Any], name: str) -> bool:
    stdlib = probe.get("stdlib")
    if not stdlib:
        return False
    base = Path(stdlib)
    return (base / f"{name}.py").is_file() or (base / name / "__init__.py").is_file()


def index_stdlib_module(conn, *, env: dict[str, Any], probe: dict[str, Any], module_name: str, force: bool = False) -> dict[str, Any]:
    stdlib = probe.get("stdlib")
    if not stdlib:
        raise IndexError_("probe did not report a stdlib path")
    top = module_name.split(".")[0]
    paths = [stdlib]
    dynload = Path(stdlib) / "lib-dynload"
    if dynload.is_dir():
        paths.append(str(dynload))
    dlls = Path(stdlib).parent / "DLLs"  # Windows layout
    if dlls.is_dir():
        paths.append(str(dlls))
    return index_python_library(
        conn,
        env=env,
        probe=probe,
        import_name=top,
        dist_name=f"stdlib/{top}",
        version=probe.get("python_version", "0"),
        search_paths=paths,
        force=force,
        source=f"stdlib:{env['id']}",
    )


def index_python_import(
    conn, *, env: dict[str, Any], probe: dict[str, Any], import_name: str, force: bool = False
) -> dict[str, Any]:
    """Index ``import_name`` whether it is a distributed package or a
    stdlib module - the one entry point the API/checker/lazy-lookup paths
    share, so the same import always maps to the same library."""
    top = import_name.split(".")[0]
    if find_distribution(probe, top) is not None:
        return index_python_library(conn, env=env, probe=probe, import_name=top, force=force)
    builtin = set(probe.get("builtin_modules") or [])
    if top in builtin or _stdlib_has(probe, top) or _compiled_module_file(
        [probe.get("stdlib") or "", str(Path(probe.get("stdlib") or ".") / "lib-dynload")], top
    ):
        return index_stdlib_module(conn, env=env, probe=probe, module_name=top, force=force)
    raise IndexError_(f"{top!r} is not installed in this environment (no distribution provides it, not in the stdlib)")


def index_python_dependency(conn, *, env: dict[str, Any], probe: dict[str, Any], dist_name: str) -> dict[str, Any] | None:
    """Index a dependency given by its *distribution* name (as written in
    pyproject/requirements: ``PyYAML``, ``scikit-learn``) via the import
    names it actually provides. Returns None when it is not installed."""
    dist = distribution_by_name(probe, dist_name)
    if dist is None:
        return None
    last = None
    for import_name in import_names_for(probe, dist):
        last = index_python_library(
            conn, env=env, probe=probe, import_name=import_name, dist_name=dist["name"], version=dist["version"]
        )
    return last


# ------------------------------------------------------ freshness / lazy --
_MISSING: dict[tuple[str, str], tuple] = {}


def current_library(conn, env_row: dict[str, Any], top: str) -> dict[str, Any] | None:
    """The up-to-date library for import name ``top`` in ``env_row``,
    indexing it (or its new version) on first need. Never raises; returns
    None when the package is not installed / cannot be read.

    Freshness: the probe is cached per interpreter until site-packages
    changes, so after ``pip install -U pandas`` the next lookup sees the new
    version, indexes it and marks the old index ``superseded``.
    """
    from . import environments

    if not env_row.get("python_path") or not top.isidentifier():
        return None
    try:
        probe = environments.probe_python(Path(env_row["python_path"]))
    except Exception:
        return None
    fingerprint = environments._probe_fingerprint(Path(env_row["python_path"]), probe)
    key = (env_row["id"], top)
    if _MISSING.get(key) == fingerprint:
        return None
    dist = find_distribution(probe, top)
    if dist is not None:
        lib_id = library_id("python", dist["name"], dist["version"], f"env:{env_row['id']}")
    else:
        lib_id = library_id("python", f"stdlib/{top}", probe.get("python_version", "0"), f"stdlib:{env_row['id']}")
    row = conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
    if row is not None and row["status"] in ("done", "partial"):
        return db.dump(row)
    try:
        return index_python_import(conn, env=env_row, probe=probe, import_name=top)
    except Exception as exc:  # noqa: BLE001
        logger.info("cannot index %s in %s: %s", top, env_row["id"], exc)
        conn.rollback()
        _MISSING[key] = fingerprint
        return None
