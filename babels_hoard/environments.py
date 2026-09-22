"""Environment detection and registration.

An "environment" is a Python interpreter and/or a node_modules folder,
registered by filesystem path. Detection never executes project code beyond
the stdlib-only probe scripts.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from . import db

PROBE_PATH = Path(__file__).parent / "probes" / "python_probe.py"


class ProbeError(RuntimeError):
    pass


def _candidate_python_paths(project_dir: Path) -> list[Path]:
    candidates = []
    for venv_name in (".venv", "venv", "env", ".env"):
        base = project_dir / venv_name
        candidates.append(base / "Scripts" / "python.exe")  # Windows
        candidates.append(base / "bin" / "python")  # POSIX
        candidates.append(base / "bin" / "python3")
    return candidates


def detect_project_python(project_dir: Path) -> Path | None:
    for candidate in _candidate_python_paths(project_dir):
        if candidate.is_file():
            return candidate
    return None


# Where a combined Python + web project usually keeps its frontend, so
# registering the project root also finds ``<root>/frontend/node_modules``.
FRONTEND_SUBDIRS = ("frontend", "web", "client", "ui", "webapp", "app")


def detect_node_modules(project_dir: Path) -> Path | None:
    candidate = project_dir / "node_modules"
    if candidate.is_dir():
        return candidate
    for sub in FRONTEND_SUBDIRS:
        candidate = project_dir / sub / "node_modules"
        if candidate.is_dir():
            return candidate
    return None


def subprocess_flags() -> dict[str, Any]:
    """Keyword arguments for helper subprocesses: on Windows, never flash a
    console window (the app itself may run without one, e.g. from Faustus)."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def looks_like_python(path: Path) -> bool:
    """Only executables named like a Python interpreter are ever run, so
    registering an arbitrary file cannot be used to execute it."""
    name = path.name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return bool(re.fullmatch(r"python(3(\.\d+)?)?w?", name)) or name == "pypy3" or name == "pypy"


_PROBE_CACHE: dict[str, tuple[tuple, dict[str, Any]]] = {}
_PROBE_LOCK = threading.Lock()


def _probe_fingerprint(python_exe: Path, probe: dict[str, Any] | None) -> tuple:
    """Cheap change detector: the mtimes of the interpreter and of every
    site-packages folder it reported. Installing, upgrading or removing a
    distribution adds/removes a ``*.dist-info`` folder there, which bumps
    the folder's mtime."""
    parts: list[Any] = []
    candidates = [python_exe]
    if probe:
        for key in ("purelib", "platlib"):
            if probe.get(key):
                candidates.append(Path(probe[key]))
        for entry in probe.get("sys_path") or []:
            if entry and ("site-packages" in entry or "dist-packages" in entry):
                candidates.append(Path(entry))
    seen = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        try:
            parts.append((key, c.stat().st_mtime_ns))
        except OSError:
            parts.append((key, None))
    return tuple(parts)


def probe_python(python_exe: Path, timeout: int = 30, use_cache: bool = True) -> dict[str, Any]:
    """Run the stdlib-only probe as a subprocess of ``python_exe``.

    Results are cached per interpreter and reused until its site-packages
    folders change, so a lookup does not pay for a subprocess each time but
    still notices a ``pip install --upgrade`` right away."""
    python_exe = Path(python_exe)
    if not python_exe.is_file():
        raise ProbeError(f"interpreter not found: {python_exe}")
    if not looks_like_python(python_exe):
        raise ProbeError(f"not a Python interpreter (expected python/python3/python.exe): {python_exe}")
    key = str(python_exe)
    if use_cache:
        with _PROBE_LOCK:
            cached = _PROBE_CACHE.get(key)
        if cached and cached[0] == _probe_fingerprint(python_exe, cached[1]):
            return cached[1]
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)  # probe the interpreter as the project sees it, not our env
    try:
        proc = subprocess.run(
            [str(python_exe), "-X", "utf8", str(PROBE_PATH)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
            **subprocess_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"probe timed out after {timeout}s") from exc
    except OSError as exc:
        raise ProbeError(f"could not run interpreter: {exc}") from exc
    if proc.returncode != 0:
        raise ProbeError(f"probe failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ProbeError(f"probe returned unparsable output: {exc}") from exc
    with _PROBE_LOCK:
        _PROBE_CACHE[key] = (_probe_fingerprint(python_exe, result), result)
    return result


def env_id_for(python_path: str | None, node_modules_path: str | None) -> str:
    raw = f"{python_path or ''}|{node_modules_path or ''}"
    return "env-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def register_environment(
    conn,
    path: str,
    label: str | None = None,
    is_builtin: bool = False,
) -> dict[str, Any]:
    """Register a project directory, a python interpreter, or a node_modules
    folder as an environment. Returns the stored environment row (dict) plus
    a ``probe`` key with the raw probe result when a Python interpreter was
    found, so callers can index dependencies right away.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise ProbeError(f"path does not exist: {p}")

    python_path: Path | None = None
    node_modules_path: Path | None = None
    project_path: Path | None = None

    if p.is_file():
        if not looks_like_python(p):
            raise ProbeError(
                f"{p} is a file but not a Python interpreter; pass a project folder, "
                "a python/python.exe path or a node_modules folder"
            )
        python_path = p
        project_path = p.parent.parent if p.parent.name in ("bin", "Scripts") else p.parent
    elif p.is_dir():
        if p.name == "node_modules":
            node_modules_path = p
            project_path = p.parent
        else:
            project_path = p
            python_path = detect_project_python(p)
            node_modules_path = detect_node_modules(p)

    if python_path is None and node_modules_path is None:
        raise ProbeError(
            f"no interpreter (.venv/venv/env) or node_modules found under {p}"
        )

    probe_result: dict[str, Any] | None = None
    python_version = None
    if python_path is not None:
        probe_result = probe_python(python_path)
        python_version = probe_result.get("python_version")

    node_version = None
    if node_modules_path is not None:
        node_version = _read_node_version(project_path)

    env_id = env_id_for(
        str(python_path) if python_path else None,
        str(node_modules_path) if node_modules_path else None,
    )
    # The same interpreter registered before (e.g. before its frontend's
    # node_modules existed or was detected) keeps its id, so its indexes and
    # the ids a model already holds stay valid.
    if python_path is not None:
        same = conn.execute(
            "SELECT id FROM environments WHERE python_path=? AND is_builtin=?", (str(python_path), int(is_builtin))
        ).fetchone()
        if same:
            env_id = same["id"]
    display_label = label or (str(project_path) if project_path else str(p))

    # created_at is the time of the latest registration: registering a
    # project again makes it the default again.
    conn.execute(
        """
        INSERT INTO environments (id, label, project_path, python_path, python_version,
                                   node_modules_path, node_version, is_builtin, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            label=excluded.label, python_version=excluded.python_version,
            node_version=excluded.node_version,
            node_modules_path=COALESCE(excluded.node_modules_path, environments.node_modules_path),
            created_at=CASE WHEN environments.is_builtin=1 THEN environments.created_at ELSE excluded.created_at END
        """,
        (
            env_id,
            display_label,
            str(project_path) if project_path else None,
            str(python_path) if python_path else None,
            python_version,
            str(node_modules_path) if node_modules_path else None,
            node_version,
            int(is_builtin),
            _registration_time(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
    result = db.dump(row)
    result["probe"] = probe_result
    return result


def _registration_time() -> str:
    """Like ``db.now()`` with milliseconds, so two registrations in the same
    second still order correctly (the newest is the default)."""
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


def _read_node_version(project_path: Path | None) -> str | None:
    if project_path is None:
        return None
    pkg = project_path / "package.json"
    if not pkg.is_file():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        return data.get("version")
    except Exception:
        return None


def builtin_env_id(conn) -> str:
    """Register (idempotently) the interpreter running Babel itself."""
    row = conn.execute("SELECT id FROM environments WHERE is_builtin=1").fetchone()
    if row:
        return row["id"]
    result = register_environment(conn, sys.executable, label="Babel's own interpreter", is_builtin=True)
    return result["id"]


def list_environments(conn) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM environments ORDER BY created_at, rowid").fetchall()
    return db.dump_all(rows)


def get_environment(conn, env_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
    return db.dump(row)


class UnknownEnvironment(ProbeError):
    pass


class NoPython(ProbeError):
    pass


_CAPABILITY = {"python": "python_path", "typescript": "node_modules_path", "js": "node_modules_path"}


def _default_env(conn, language: str | None) -> dict[str, Any] | None:
    column = _CAPABILITY.get(language or "")
    where = f" AND {column} IS NOT NULL" if column else ""
    row = conn.execute(
        f"SELECT * FROM environments WHERE is_builtin=0{where} ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return db.dump(row)


def resolve_env(conn, env: str | None, language: str | None = None) -> dict[str, Any]:
    """Resolve ``env`` (an id, a project path, or None) to an environment row.

    None means the default *for the language*: the most recently registered
    project that has a Python interpreter (``language="python"``) or a
    node_modules folder (``"typescript"``), falling back to the builtin
    interpreter. Without a language: the most recently registered project.
    A frontend-only environment is therefore never the default for Python
    checks and lookups.
    """
    if env:
        row = conn.execute("SELECT * FROM environments WHERE id=?", (env,)).fetchone()
        if row:
            return db.dump(row)
        # Maybe it's a path that is already registered.
        p = str(Path(env).expanduser())
        row = conn.execute(
            "SELECT * FROM environments WHERE project_path=? OR python_path=? OR node_modules_path=? "
            "ORDER BY python_path IS NULL, created_at DESC",
            (p, p, p),
        ).fetchone()
        if row:
            return db.dump(row)
        known = ", ".join(f"{r['id']} ({r['label']})" for r in list_environments(conn)[-5:])
        raise UnknownEnvironment(
            f"unknown environment: {env!r}. If it is a project folder, register it first with "
            f"docs_add_environment(path); otherwise pass an env id from docs_libraries (known: {known})."
        )
    row = _default_env(conn, language)
    if row is None and language in ("typescript", "js"):
        row = _default_env(conn, None)
    if row:
        return row
    row = conn.execute("SELECT * FROM environments WHERE is_builtin=1 LIMIT 1").fetchone()
    if row:
        return db.dump(row)
    env_id = builtin_env_id(conn)
    return get_environment(conn, env_id)


def require_python(conn, env_row: dict[str, Any]) -> None:
    """Python checks/lookups need an interpreter; a node_modules-only
    environment would silently verify nothing."""
    if env_row.get("python_path"):
        return
    others = [r for r in list_environments(conn) if r.get("python_path")]
    choices = ", ".join(f"{r['id']} ({r['label']})" for r in others[-4:]) or "none registered"
    raise NoPython(
        f"environment {env_row['id']} ({env_row['label']}) has no Python interpreter, only node_modules, "
        f"so Python code cannot be checked against it. Pass env=<id> of a Python environment ({choices}), "
        "or omit env to use the default Python environment."
    )


def compact_env(row: dict[str, Any]) -> dict[str, Any]:
    """The environment fields a model needs (no timestamps / internals)."""
    return {
        "id": row["id"],
        "label": row["label"],
        "python": row.get("python_version"),
        "python_path": row.get("python_path"),
        "node_modules_path": row.get("node_modules_path"),
        "builtin": bool(row.get("is_builtin")),
    }


def default_ids(conn) -> dict[str, str]:
    """{language: env id used when a call omits env}."""
    return {lang: resolve_env(conn, None, lang)["id"] for lang in ("python", "typescript")}


def compact_environments(conn) -> list[dict[str, Any]]:
    defaults = default_ids(conn)
    out = []
    for row in list_environments(conn):
        item = compact_env(row)
        item["default"] = row["id"] == defaults["python"]
        default_for = [
            lang for lang, env_id in defaults.items()
            if env_id == row["id"] and row.get(_CAPABILITY[lang])
        ]
        if default_for:
            item["default_for"] = default_for
        out.append(item)
    return out


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_packages(conn, env_row: dict[str, Any], *, query: str = "", limit: int = 200) -> dict[str, Any]:
    """Distributions installed in the environment's interpreter, joined with
    their index status in Babel (for the Libraries page)."""
    if not env_row.get("python_path"):
        return {"packages": [], "total": 0, "truncated": False}
    probe = probe_python(Path(env_row["python_path"]))
    rows = conn.execute(
        "SELECT name, version, status, entry_count FROM libraries WHERE env_id=? AND ecosystem='python'",
        (env_row["id"],),
    ).fetchall()
    status_by = {}
    for r in rows:
        status_by[(_norm(r["name"]), r["version"])] = (r["status"], r["entry_count"])
    q = query.strip().lower()
    items = []
    for dist in sorted(probe.get("distributions", []), key=lambda d: d["name"].lower()):
        if q and q not in dist["name"].lower():
            continue
        status, count = status_by.get((_norm(dist["name"]), dist["version"]), (None, 0))
        items.append(
            {
                "name": dist["name"],
                "version": dist["version"],
                "import_names": [t for t in dist.get("top_level", []) if t and not t.startswith("_")][:5],
                "status": status or "not_indexed",
                "entry_count": count,
            }
        )
    limit = max(1, min(limit, 1000))
    return {"packages": items[:limit], "total": len(items), "truncated": len(items) > limit}
