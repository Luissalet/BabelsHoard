"""JS/TS indexing: shells out to ``probes/ts_probe.mjs`` (TypeScript compiler
API) to read a package's type declarations from a ``node_modules`` folder.
Never runs project code — only reads ``.d.ts`` files."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import db
from .environments import subprocess_flags
from .indexing import INDEX_LOCK, library_id

# npm package names: optional @scope/, lowercase-ish, no path tricks.
_PKG_RE = re.compile(r"^(@[a-z0-9][\w.\-~]*/)?[a-z0-9][\w.\-~]*$", re.I)

PROBE_DIR = Path(__file__).parent / "probes"
PROBE_SCRIPT = PROBE_DIR / "ts_probe.mjs"
DOC_CAP = 8000


class NodeIndexError(RuntimeError):
    pass


def node_available() -> str | None:
    return shutil.which("node")


def ensure_probe_deps(timeout: int = 120) -> None:
    """Install the probe's own npm deps (typescript) if missing. Committed
    package.json + package-lock.json make this reproducible."""
    if (PROBE_DIR / "node_modules" / "typescript").exists():
        return
    npm = shutil.which("npm")
    if not npm:
        raise NodeIndexError("npm not found; cannot install the TypeScript compiler API")
    proc = subprocess.run(
        [npm, "ci", "--no-audit", "--no-fund"],
        cwd=str(PROBE_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        **subprocess_flags(),
    )
    if proc.returncode != 0:
        raise NodeIndexError(f"npm ci failed: {proc.stderr.strip()[:500]}")


def run_probe(node_modules_dir: Path, package_name: str, timeout: int = 60) -> dict[str, Any]:
    node = node_available()
    if not node:
        raise NodeIndexError("Node.js not found")
    ensure_probe_deps()
    try:
        proc = subprocess.run(
            [node, str(PROBE_SCRIPT), str(node_modules_dir), package_name],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            **subprocess_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise NodeIndexError(f"probe timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise NodeIndexError(f"probe crashed: {proc.stderr.strip()[:500]}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise NodeIndexError(f"probe returned unparsable output: {exc}") from exc


def valid_package_name(name: str) -> bool:
    return bool(_PKG_RE.match(name)) and ".." not in name


def installed_version(node_modules: Path, package_name: str) -> str | None:
    pkg_json_path = node_modules / package_name / "package.json"
    if not pkg_json_path.is_file():
        return None
    try:
        return json.loads(pkg_json_path.read_text(encoding="utf-8")).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


def current_js_library(conn, env: dict[str, Any], package_name: str) -> dict[str, Any] | None:
    """The index of the version of ``package_name`` installed right now in
    the environment's node_modules, built on first need. None when the
    package, its types or Node.js are missing (the checker then stays
    silent)."""
    if not env.get("node_modules_path") or not valid_package_name(package_name):
        return None
    node_modules = Path(env["node_modules_path"])
    version = installed_version(node_modules, package_name)
    if version is None:
        return None
    lib_id = library_id("js", package_name, version, f"node_modules:{env['id']}")
    row = conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
    if row is not None and row["status"] in ("done", "partial", "error"):
        return db.dump(row)
    try:
        return index_js_library(conn, env=env, package_name=package_name)
    except Exception:  # noqa: BLE001 - no node, no types: stay silent
        conn.rollback()
        return None


def index_js_library(conn, *, env: dict[str, Any], package_name: str, force: bool = False) -> dict[str, Any]:
    if not valid_package_name(package_name):
        raise NodeIndexError(f"not an npm package name: {package_name!r}")
    node_modules = Path(env["node_modules_path"])
    source = f"node_modules:{env['id']}"
    version = installed_version(node_modules, package_name)
    if version is None:
        raise NodeIndexError(f"package not found in {node_modules}: {package_name}")
    with INDEX_LOCK:
        return _index_js_locked(conn, env, node_modules, source, package_name, version, force)


def _index_js_locked(conn, env, node_modules: Path, source: str, package_name: str, version: str, force: bool) -> dict[str, Any]:
    lib_id = library_id("js", package_name, version, source)
    existing = conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone()
    if existing and existing["status"] in ("done", "partial") and not force:
        return db.dump(existing)

    conn.execute(
        """
        INSERT INTO libraries (id, ecosystem, name, version, source, env_id, status, created_at)
        VALUES (?, 'js', ?, ?, ?, ?, 'indexing', ?)
        ON CONFLICT(id) DO UPDATE SET status='indexing'
        """,
        (lib_id, package_name, version, source, env["id"], db.now()),
    )
    conn.commit()

    try:
        result = run_probe(node_modules, package_name)
    except NodeIndexError as exc:
        # Tooling problem (no Node.js/npm, timeout): nothing is known about
        # the package, so do not leave a row that looks like an answer.
        if existing is None:
            conn.execute("DELETE FROM libraries WHERE id=?", (lib_id,))
        else:
            conn.execute("UPDATE libraries SET status='error', note=? WHERE id=?", (str(exc)[:500], lib_id))
        conn.commit()
        raise

    if result.get("error"):
        conn.execute("UPDATE libraries SET status='error', note=? WHERE id=?", (result["error"][:500], lib_id))
        conn.commit()
        raise NodeIndexError(result["error"])

    rows: list[tuple] = []
    for exp in result.get("exports", []):
        qualname = f"{package_name}.{exp['name']}"
        doc = exp.get("jsdoc") or None
        if doc and len(doc) > DOC_CAP:
            doc = doc[:DOC_CAP] + "\n... (truncated)"
        rows.append(
            (
                f"{lib_id}:{qualname}",
                lib_id,
                exp["name"],
                qualname,
                exp.get("kind", "value"),
                exp.get("signature"),
                None,
                None,
                (doc.splitlines()[0] if doc else None),
                doc,
                int(bool(exp.get("deprecated"))),
                "deprecated" if exp.get("deprecated") else None,
                exp.get("file"),
                exp.get("line"),
                None,
            )
        )
        for m in exp.get("members", []):
            member_qual = f"{qualname}.{m['name']}"
            mdoc = m.get("jsdoc")
            rows.append(
                (
                    f"{lib_id}:{member_qual}",
                    lib_id,
                    m["name"],
                    member_qual,
                    "attribute",
                    m.get("signature"),
                    None,
                    None,
                    (mdoc.splitlines()[0] if mdoc else None),
                    mdoc,
                    int(bool(m.get("deprecated"))),
                    "deprecated" if m.get("deprecated") else None,
                    exp.get("file"),
                    None,
                    f"{lib_id}:{qualname}",
                )
            )
    conn.execute("DELETE FROM entries WHERE library_id=?", (lib_id,))
    conn.executemany(
        """
        INSERT OR REPLACE INTO entries
        (id, library_id, name, qualname, kind, signature, params_json, returns,
         summary, doc, deprecated, deprecated_note, source_path, source_line, parent_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    status = "partial" if result.get("truncated") else "done"
    conn.execute(
        "UPDATE libraries SET status=?, entry_count=?, note=?, indexed_at=? WHERE id=?",
        (status, len(rows), f"indexed {len(rows)} entries from {result.get('entry')}", db.now(), lib_id),
    )
    conn.execute(
        "UPDATE libraries SET status='superseded' WHERE ecosystem='js' AND name=? AND source=? AND id != ? "
        "AND status IN ('done', 'partial')",
        (package_name, source, lib_id),
    )
    conn.commit()
    return db.dump(conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())
