"""Read a project's direct dependencies from pyproject.toml /
requirements*.txt next to its environment, for "index project dependencies".
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+")


def _clean_name(spec: str) -> str | None:
    spec = spec.strip()
    if not spec or spec.startswith(("#", "-")):
        return None
    spec = spec.split(";")[0].strip()  # drop environment markers
    m = _NAME_RE.match(spec)
    if not m:
        return None
    return m.group(0).replace("_", "-").lower()


# Groups and files that only exist for development. Their packages are still
# indexed on demand when code imports them; the eager dependency job skips
# them because they are big (mypy alone hits the entry cap) and application
# code rarely calls them.
_DEV_GROUPS = {"dev", "develop", "development", "test", "tests", "testing", "lint", "linting", "typing", "types", "docs", "doc"}
_TOOL_NAMES = {
    "pytest", "mypy", "ruff", "black", "flake8", "pylint", "isort", "pre-commit", "coverage", "tox", "nox",
    "pyright", "bandit", "autopep8", "yapf", "codespell", "pip-tools", "hatch", "twine", "build",
}
_TOOL_PREFIXES = ("pytest-", "types-", "flake8-", "mypy-")


def is_dev_tool(name: str) -> bool:
    return name in _TOOL_NAMES or name.startswith(_TOOL_PREFIXES)


def split_dependencies(project_path: Path) -> tuple[list[str], list[str]]:
    """(runtime, dev) direct dependency names of a project."""
    runtime: set[str] = set()
    dev: set[str] = set()

    def add(spec: str, is_dev: bool) -> None:
        n = _clean_name(spec)
        if n:
            (dev if is_dev or is_dev_tool(n) else runtime).add(n)

    pyproject = project_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        project = data.get("project", {})
        for spec in project.get("dependencies", []) or []:
            add(spec, False)
        for group, specs in (project.get("optional-dependencies", {}) or {}).items():
            for spec in specs:
                add(spec, group.lower() in _DEV_GROUPS)
        # PEP 735 dependency groups (entries may also be {include-group = ...})
        for group, specs in (data.get("dependency-groups", {}) or {}).items():
            for spec in specs:
                if isinstance(spec, str):
                    add(spec, group.lower() in _DEV_GROUPS)
        # Poetry: [tool.poetry.dependencies] and [tool.poetry.group.<g>.dependencies]
        poetry = (data.get("tool", {}) or {}).get("poetry", {}) or {}
        tables = [(poetry.get("dependencies", {}) or {}, False), (poetry.get("dev-dependencies", {}) or {}, True)]
        tables += [
            ((g or {}).get("dependencies", {}) or {}, name.lower() in _DEV_GROUPS)
            for name, g in (poetry.get("group", {}) or {}).items()
        ]
        for table, is_dev in tables:
            for key in table:
                if key.lower() != "python":
                    add(key, is_dev)
    for req_file in project_path.glob("requirements*.txt"):
        stem = req_file.stem.lower()
        is_dev = any(part in _DEV_GROUPS for part in re.split(r"[-_.]", stem)[1:])
        try:
            for line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
                add(line, is_dev)
        except OSError:
            continue
    return sorted(runtime), sorted(dev - runtime)


def direct_dependencies(project_path: Path, include_dev: bool = False) -> list[str]:
    runtime, dev = split_dependencies(project_path)
    return sorted({*runtime, *dev}) if include_dev else runtime
