"""Offline documentation from the public DevDocs mirror.

``catalog()`` lists installable docsets (network); ``install()`` downloads
one, converts each page to Markdown and indexes it by section anchor so it
is searchable and readable offline afterwards. Tests never hit the network:
they build a tiny fixture docset and call ``install_from_data`` directly.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from . import db
from .hoard_link.web.fetch import Fetcher
from .hoard_link.web.htmltext import to_markdown
from .indexing import INDEX_LOCK, library_id

CATALOG_URL = "https://devdocs.io/docs.json"
DOCUMENTS_BASE = "https://documents.devdocs.io"
DOC_CAP = 8000

# The biggest DevDocs databases are a few hundred MB of JSON; the shared fetcher stops at its byte cap, so say it.
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024
INDEX_TIMEOUT_S = 60.0
DB_TIMEOUT_S = 300.0
CATALOG_TIMEOUT_S = 30.0

_ID_RE = re.compile(r'id="([^"]+)"')
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_.~\-]{0,80}$")


def valid_slug(slug: str) -> bool:
    """DevDocs slugs look like ``python~3.12`` or ``node~22_lts``; anything
    else (paths, URLs) is refused before it reaches a download URL."""
    return bool(_SLUG_RE.match(slug or "")) and ".." not in slug


class DocsetDownloadError(RuntimeError):
    """A download from the DevDocs mirror failed (network, a bad status, a body that is not JSON, or one over the cap)."""


def _fetcher() -> Fetcher:
    """The shared polite fetcher (one browser-like User-Agent for the family, a byte cap, one retry with backoff, SSRF
    policy at every redirect). Tests replace this function to inject a mock transport."""
    return Fetcher(max_bytes=MAX_DOWNLOAD_BYTES, min_interval_s=0.5, retries=2)


def _get_json(fetcher: Fetcher, url: str, timeout: float) -> Any:
    result, data = fetcher.get_json(url, timeout=timeout)
    if result.truncated:
        raise DocsetDownloadError(f"{url}: larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB, not downloaded")
    if not result.ok:
        raise DocsetDownloadError(f"{url}: {result.error or 'HTTP ' + str(result.status)}")
    return data


def html_fragment_to_markdown(html: str) -> str:
    """Markdown of one HTML fragment (the shared converter: ``hoard_link.web.htmltext.to_markdown``)."""
    return to_markdown(html)["markdown"]


def catalog(query: str = "", limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(limit, 50))
    data = _get_json(_fetcher(), CATALOG_URL, CATALOG_TIMEOUT_S)
    q = query.strip().lower()
    if q:
        data = [d for d in data if q in d["name"].lower() or q in d["slug"].lower()]
    total = len(data)
    items = [
        {
            "slug": d["slug"],
            "name": d["name"],
            "version": d.get("version") or d.get("release") or "",
            "db_size_kb": round((d.get("db_size") or 0) / 1024),
        }
        for d in data[:limit]
    ]
    return {"query": query, "results": items, "count": len(items), "total": total, "truncated": total > limit}


def _split_page_into_sections(page_key: str, html: str, wanted_anchors: dict[str, str]) -> dict[str, str]:
    """Returns {anchor_or_"": markdown_text} for the anchors present in this
    page's HTML. ``""`` is used for the whole-page fallback."""
    anchors: list[tuple[int, str]] = []
    for m in _ID_RE.finditer(html):
        anchor_id = m.group(1)
        if anchor_id not in wanted_anchors:
            continue
        tag_start = html.rfind("<", 0, m.start())
        if tag_start == -1:
            continue
        anchors.append((tag_start, anchor_id))
    anchors.sort()
    out: dict[str, str] = {}
    for i, (start, anchor_id) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(html)
        out[anchor_id] = html_fragment_to_markdown(html[start:end])
    if not anchors:
        out[""] = html_fragment_to_markdown(html)
    return out


def install_from_data(
    conn,
    slug: str,
    name: str,
    version: str,
    index_data: dict[str, Any],
    db_data: dict[str, str],
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    lib_id = library_id("docset", slug, version or "0", "devdocs")
    entries = index_data.get("entries", [])
    by_page: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        page = e["path"].split("#", 1)[0]
        by_page.setdefault(page, []).append(e)

    # Convert everything first (CPU-bound, no database transaction open).
    rows: list[tuple] = []
    seen_ids: set[str] = set()
    total_pages = max(1, len(by_page))
    for i, (page, page_entries) in enumerate(by_page.items()):
        html = db_data.get(page)
        if html is None:
            continue
        wanted = {e["path"].split("#", 1)[1] if "#" in e["path"] else "": e for e in page_entries}
        sections = _split_page_into_sections(page, html, {k: k for k in wanted if k})
        for e in page_entries:
            anchor = e["path"].split("#", 1)[1] if "#" in e["path"] else ""
            text = sections.get(anchor) or sections.get("") or ""
            if len(text) > DOC_CAP:
                text = text[:DOC_CAP] + "\n... (truncated)"
            summary = text.strip().splitlines()[0][:200] if text.strip() else e["name"]
            entry_id = f"{lib_id}:{e['path']}"
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            rows.append(
                (entry_id, lib_id, e["name"], e["name"], "section", None, None, None, summary, text,
                 0, None, e["path"], None, None)
            )
        if progress and (i % 25 == 0 or i + 1 == total_pages):
            progress(0.2 + 0.7 * (i + 1) / total_pages, f"converted {i + 1}/{total_pages} pages")

    with INDEX_LOCK:
        conn.execute(
            """
            INSERT INTO libraries (id, ecosystem, name, version, source, status, created_at)
            VALUES (?, 'docset', ?, ?, 'devdocs', 'indexing', ?)
            ON CONFLICT(id) DO UPDATE SET status='indexing'
            """,
            (lib_id, name, version or "0", db.now()),
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
            "UPDATE libraries SET status='done', entry_count=?, note=?, indexed_at=? WHERE id=?",
            (len(rows), f"{len(rows)} sections from {len(by_page)} pages (devdocs.io, original licences apply)", db.now(), lib_id),
        )
        old = conn.execute("SELECT library_id FROM docsets WHERE slug=?", (slug,)).fetchone()
        conn.execute(
            """
            INSERT INTO docsets (slug, name, version, library_id, installed_at, entry_count)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET version=excluded.version, library_id=excluded.library_id,
                installed_at=excluded.installed_at, entry_count=excluded.entry_count
            """,
            (slug, name, version, lib_id, db.now(), len(rows)),
        )
        if old and old["library_id"] and old["library_id"] != lib_id:
            conn.execute("DELETE FROM libraries WHERE id=?", (old["library_id"],))  # previous release
        conn.commit()
    return db.dump(conn.execute("SELECT * FROM libraries WHERE id=?", (lib_id,)).fetchone())


def install(conn, slug: str, progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    if not valid_slug(slug):
        raise ValueError(f"not a docset slug: {slug!r}")
    fetcher = _fetcher()
    all_docs = _get_json(fetcher, CATALOG_URL, CATALOG_TIMEOUT_S)
    meta = next((d for d in all_docs if d["slug"] == slug), None)
    if meta is None:
        raise ValueError(f"unknown docset slug: {slug!r}")
    if progress:
        progress(0.05, "downloading index")
    index_data = _get_json(fetcher, f"{DOCUMENTS_BASE}/{slug}/index.json", INDEX_TIMEOUT_S)
    if progress:
        progress(0.1, f"downloading pages ({meta.get('db_size', 0) // 1_000_000} MB)")
    db_data = _get_json(fetcher, f"{DOCUMENTS_BASE}/{slug}/db.json", DB_TIMEOUT_S)
    return install_from_data(
        conn, slug, meta["name"], meta.get("version") or meta.get("release") or "", index_data, db_data, progress
    )


def list_installed(conn) -> list[dict[str, Any]]:
    return db.dump_all(conn.execute("SELECT * FROM docsets ORDER BY name").fetchall())
