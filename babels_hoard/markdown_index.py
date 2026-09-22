"""Index a folder of ``.md``/``.mdx``/``.rst``/``.txt`` files, split by
heading, as a ``markdown`` library."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import db
from .indexing import library_id

DOC_CAP = 8000
_MD_EXT = {".md", ".mdx", ".rst", ".txt"}
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def _split_markdown(text: str) -> list[tuple[str, str]]:
    """Returns [(heading_or_empty, body)]."""
    lines = text.splitlines()
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            sections.append((m.group(2).strip(), []))
        else:
            sections[-1][1].append(line)
    return [(h, "\n".join(b).strip()) for h, b in sections if h or "\n".join(b).strip()]


def index_folder(conn, path: str, name: str | None = None) -> dict[str, Any]:
    root = Path(path).expanduser()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    display_name = name or root.name
    lib_id = library_id("markdown", display_name, "0", f"folder:{root}")
    conn.execute(
        """
        INSERT INTO libraries (id, ecosystem, name, version, source, status, created_at)
        VALUES (?, 'markdown', ?, '0', ?, 'indexing', ?)
        ON CONFLICT(id) DO UPDATE SET status='indexing'
        """,
        (lib_id, display_name, f"folder:{root}", db.now()),
    )
    conn.execute("DELETE FROM entries WHERE library_id=?", (lib_id,))

    rows: list[tuple] = []
    files = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _MD_EXT]
    for fpath in files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = fpath.relative_to(root).as_posix()
        for heading, body in _split_markdown(text):
            title = heading or rel
            qualname = f"{rel}#{heading}" if heading else rel
            doc = body
            if len(doc) > DOC_CAP:
                doc = doc[:DOC_CAP] + "\n... (truncated)"
            summary = (body.strip().splitlines()[0] if body.strip() else title)[:200]
            rows.append(
                (
                    f"{lib_id}:{qualname}",
                    lib_id,
                    title,
                    qualname,
                    "section",
                    None,
                    None,
                    None,
                    summary,
                    doc,
                    0,
                    None,
                    str(fpath),
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
    conn.execute(
        "UPDATE libraries SET status='done', entry_count=?, note=?, indexed_at=? WHERE id=?",
        (len(rows), f"{len(rows)} sections from {len(files)} files", db.now(), lib_id),
    )
    conn.commit()
    return db.dump(conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())
