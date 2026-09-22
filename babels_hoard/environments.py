"""Environment detection and registration.

An "environment" is a Python interpreter and/or a node_modules folder,
registered by filesystem path. Detection never executes project code beyond
the stdlib-only probe scripts.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
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


def detect_node_modules(project_dir: Path) -> Path | None:
    candidate = project_dir / "node_modules"
    if candidate.is_dir():
        return candidate
    return None


def probe_python(python_exe: Path, timeout: int = 30) -> dict[str, Any]:
    """Run the stdlib-only probe as a subprocess of ``python_exe``."""
    if not python_exe.is_file():
        raise ProbeError(f"interpreter not found: {python_exe}")
    try:
        proc = subprocess.run(
            [str(python_exe), str(PROBE_PATH)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"probe timed out after {timeout}s") from exc
    except OSError as exc:
        raise ProbeError(f"could not run interpreter: {exc}") from exc
    if proc.returncode != 0:
        raise ProbeError(f"probe failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ProbeError(f"probe returned unparsable output: {exc}") from exc


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
        # Assume it's a python executable.
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
    display_label = label or (str(project_path) if project_path else str(p))

    conn.execute(
        """
        INSERT INTO environments (id, label, project_path, python_path, python_version,
                                   node_modules_path, node_version, is_builtin, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            label=excluded.label, python_version=excluded.python_version,
            node_version=excluded.node_version
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
            db.now(),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
    result = db.dump(row)
    result["probe"] = probe_result
    return result


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
    rows = conn.execute("SELECT * FROM environments ORDER BY created_at").fetchall()
    return db.dump_all(rows)


def get_environment(conn, env_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM environments WHERE id=?", (env_id,)).fetchone()
    return db.dump(row)


def resolve_env(conn, env: str | None) -> dict[str, Any]:
    """Resolve ``env`` (an id, a project path, or None) to an environment row.

    None means: the most recently registered project environment, falling
    back to the builtin interpreter.
    """
    if env:
        row = conn.execute("SELECT * FROM environments WHERE id=?", (env,)).fetchone()
        if row:
            return db.dump(row)
        # Maybe it's a path that is already registered.
        p = str(Path(env).expanduser())
        row = conn.execute(
            "SELECT * FROM environments WHERE project_path=? OR python_path=?", (p, p)
        ).fetchone()
        if row:
            return db.dump(row)
        raise ProbeError(f"unknown environment: {env!r}")
    row = conn.execute(
        "SELECT * FROM environments WHERE is_builtin=0 ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    if row:
        return db.dump(row)
    row = conn.execute("SELECT * FROM environments WHERE is_builtin=1 LIMIT 1").fetchone()
    if row:
        return db.dump(row)
    env_id = builtin_env_id(conn)
    return get_environment(conn, env_id)
