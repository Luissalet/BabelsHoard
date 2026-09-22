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


def direct_dependencies(project_path: Path) -> list[str]:
    names: set[str] = set()
    pyproject = project_path / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        project = data.get("project", {})
        for spec in project.get("dependencies", []) or []:
            n = _clean_name(spec)
            if n:
                names.add(n)
        for group in (project.get("optional-dependencies", {}) or {}).values():
            for spec in group:
                n = _clean_name(spec)
                if n:
                    names.add(n)
    for req_file in project_path.glob("requirements*.txt"):
        try:
            for line in req_file.read_text(encoding="utf-8", errors="replace").splitlines():
                n = _clean_name(line)
                if n:
                    names.add(n)
        except OSError:
            continue
    return sorted(names)
