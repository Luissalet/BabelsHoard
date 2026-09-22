"""Stdlib-only probe, run as a subprocess *of the target interpreter*.

Never import user packages: this script only inspects installed
distribution metadata, it does not run any project code. Prints one JSON
object to stdout.
"""
from __future__ import annotations

import json
import os
import sys
import sysconfig


def _top_level_names(dist) -> list[str]:
    try:
        text = dist.read_text("top_level.txt")
    except Exception:
        text = None
    if text:
        return [line.strip() for line in text.splitlines() if line.strip()]
    # Fall back to deriving top-level import names from RECORD.
    names: set[str] = set()
    try:
        record = dist.read_text("RECORD") or ""
    except Exception:
        record = ""
    for line in record.splitlines():
        path = line.split(",")[0].replace("\\", "/")
        if not path or path.startswith("..") or "/" not in path and not path.endswith(".py"):
            continue
        first = path.split("/")[0]
        if first.endswith((".dist-info", ".egg-info", ".data")) or first == "__pycache__":
            continue
        if path.endswith((".py", ".pyi", ".pyd", ".so")):
            names.add(first.split(".")[0])
    return sorted(n for n in names if n and n.isidentifier())


def main() -> None:
    import importlib.metadata as im

    distributions = []
    seen = set()
    for dist in im.distributions():
        try:
            name = dist.metadata["Name"]
        except Exception:
            name = None
        if not name:
            continue
        key = (name, dist.version)
        if key in seen:
            continue
        seen.add(key)
        distributions.append(
            {
                "name": name,
                "version": dist.version or "0.0.0",
                "top_level": _top_level_names(dist),
                "location": str(getattr(dist, "_path", "") or ""),
            }
        )

    paths = sysconfig.get_paths()
    # sys.path[0] is this script's own folder when run as a script; it is
    # not part of the target environment.
    here = os.path.dirname(os.path.abspath(__file__))
    sys_path = [p for p in sys.path if p and os.path.abspath(p) != here]
    result = {
        "python_version": sys.version.split()[0],
        "sys_version": sys.version,
        "executable": sys.executable,
        "sys_path": sys_path,
        "stdlib": paths.get("stdlib"),
        "platlib": paths.get("platlib"),
        "purelib": paths.get("purelib"),
        "builtin_modules": sorted(sys.builtin_module_names),
        "distributions": distributions,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
