"""Index a folder of ``.md``/``.mdx``/``.rst``/``.txt`` files, split by
heading, as a ``markdown`` library.

Bounded so that pointing it at a whole project cannot index a
``node_modules`` tree by accident: dependency/VCS/build folders are skipped,
and there are caps on file count and file size (reported in the note).
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import db
from .indexing import INDEX_LOCK, library_id

DOC_CAP = 8000
MAX_FILES = 2000
MAX_FILE_BYTES = 2_000_000
_MD_EXT = {".md", ".mdx", ".rst", ".txt"}
_SKIP_DIRS = {
    "node_modules", ".git", ".hg", ".svn", ".venv", "venv", "env", "__pycache__", ".tox",
    ".mypy_cache", ".pytest_cache", "dist", "build", "site-packages", ".next", ".cache",
}
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_RST_UNDERLINE_RE = re.compile(r"^([=\-~^\"'`#*+])\1{2,}\s*$")


def _split_markdown(text: str, rst: bool = False) -> list[tuple[str, str]]:
    """Returns [(heading_or_empty, body)]. Lines inside fenced code blocks
    are never headings (``# comment`` in a bash block is not a section).
    For ``.rst``, a line followed by an ``====``/``----`` underline is a
    heading."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            sections[-1][1].append(line)
            i += 1
            continue
        if not in_fence:
            m = _HEADING_RE.match(line)
            if m and not rst:
                sections.append((m.group(2).strip(), []))
                i += 1
                continue
            if (
                rst
                and line.strip()
                and i + 1 < len(lines)
                and _RST_UNDERLINE_RE.match(lines[i + 1])
                and len(lines[i + 1].strip()) >= len(line.strip())
            ):
                sections.append((line.strip(), []))
                i += 2
                continue
        sections[-1][1].append(line)
        i += 1
    return [(h, "\n".join(b).strip()) for h, b in sections if h or "\n".join(b).strip()]


def _iter_files(root: Path) -> tuple[list[Path], int]:
    files: list[Path] = []
    skipped_big = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for fn in sorted(filenames):
            if Path(fn).suffix.lower() not in _MD_EXT:
                continue
            fpath = Path(dirpath) / fn
            try:
                if fpath.stat().st_size > MAX_FILE_BYTES:
                    skipped_big += 1
                    continue
            except OSError:
                continue
            files.append(fpath)
            if len(files) >= MAX_FILES:
                return files, skipped_big
    return files, skipped_big


def index_folder(conn, path: str, name: str | None = None) -> dict[str, Any]:
    root = Path(path).expanduser()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    root = root.resolve()
    display_name = (name or root.name or str(root)).strip()[:120]
    lib_id = library_id("markdown", display_name, "0", f"folder:{root}")

    files, skipped_big = _iter_files(root)
    rows: list[tuple] = []
    seen: dict[str, int] = {}
    for fpath in files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = fpath.relative_to(root).as_posix()
        for heading, body in _split_markdown(text, rst=fpath.suffix.lower() == ".rst"):
            title = heading or rel
            qualname = f"{rel}#{heading}" if heading else rel
            n = seen.get(qualname, 0)
            seen[qualname] = n + 1
            if n:
                qualname = f"{qualname} ({n + 1})"  # repeated heading in the same file
            doc = body[:DOC_CAP] + "\n... (truncated)" if len(body) > DOC_CAP else body
            summary = (body.strip().splitlines()[0] if body.strip() else title)[:200]
            rows.append(
                (f"{lib_id}:{qualname}", lib_id, title, qualname, "section", None, None, None,
                 summary, doc, 0, None, str(fpath), None, None)
            )

    note = f"{len(rows)} sections from {len(files)} files"
    if len(files) >= MAX_FILES:
        note += f" (stopped at {MAX_FILES} files)"
    if skipped_big:
        note += f"; {skipped_big} files over {MAX_FILE_BYTES // 1_000_000} MB skipped"
    with INDEX_LOCK:
        conn.execute(
            """
            INSERT INTO libraries (id, ecosystem, name, version, source, status, created_at)
            VALUES (?, 'markdown', ?, '0', ?, 'indexing', ?)
            ON CONFLICT(id) DO UPDATE SET status='indexing'
            """,
            (lib_id, display_name, f"folder:{root}", db.now()),
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
        conn.execute(
            "UPDATE libraries SET status=?, entry_count=?, note=?, indexed_at=? WHERE id=?",
            ("partial" if len(files) >= MAX_FILES else "done", len(rows), note, db.now(), lib_id),
        )
        conn.commit()
    row = db.dump(conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "entry_count": row["entry_count"],
        "note": row["note"],
        "hint": "Search it with docs_search(query, library=<name>); read a section with docs_read(id).",
    }
