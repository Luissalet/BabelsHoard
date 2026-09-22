"""JS/TS indexing: shells out to ``probes/ts_probe.mjs`` (TypeScript compiler
API) to read a package's type declarations from a ``node_modules`` folder.
Never runs project code — only reads ``.d.ts`` files."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import db
from .indexing import library_id

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
        timeout=timeout,
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
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise NodeIndexError(f"probe timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise NodeIndexError(f"probe crashed: {proc.stderr.strip()[:500]}")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise NodeIndexError(f"probe returned unparsable output: {exc}") from exc


def index_js_library(conn, *, env: dict[str, Any], package_name: str, force: bool = False) -> dict[str, Any]:
    node_modules = Path(env["node_modules_path"])
    source = f"node_modules:{env['id']}"

    pkg_json_path = node_modules / package_name / "package.json"
    version = "0.0.0"
    if pkg_json_path.is_file():
        try:
            version = json.loads(pkg_json_path.read_text(encoding="utf-8")).get("version", "0.0.0")
        except Exception:
            pass

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
        conn.execute("UPDATE libraries SET status='error', note=? WHERE id=?", (str(exc)[:500], lib_id))
        conn.commit()
        raise

    if result.get("error"):
        conn.execute("UPDATE libraries SET status='error', note=? WHERE id=?", (result["error"][:500], lib_id))
        conn.commit()
        raise NodeIndexError(result["error"])

    conn.execute("DELETE FROM entries WHERE library_id=?", (lib_id,))
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
                    None,
                )
            )
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
    conn.commit()
    return db.dump(conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())
