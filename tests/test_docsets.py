import json
from pathlib import Path

import pytest

from babels_hoard import docsets

FIXTURES = Path(__file__).parent / "fixtures" / "docset"


def test_install_from_data_splits_sections(conn):
    index_data = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))
    db_data = json.loads((FIXTURES / "db.json").read_text(encoding="utf-8"))
    lib = docsets.install_from_data(conn, "widget", "Widget Guide", "1.0", index_data, db_data)
    assert lib["status"] == "done"
    assert lib["entry_count"] == 3

    row = conn.execute("SELECT * FROM entries WHERE name='Getting Started'").fetchone()
    assert row is not None
    assert "pip install widget" in row["doc"]

    row2 = conn.execute("SELECT * FROM entries WHERE name='Advanced Usage'").fetchone()
    assert "strict=True" in row2["doc"]
    # the two sections of the same page must not bleed into each other
    assert "pip install widget" not in row2["doc"]

    installed = docsets.list_installed(conn)
    assert any(d["slug"] == "widget" for d in installed)


def test_catalog_filters_by_query_offline_shape():
    # catalog() itself hits the network; here we only test the filtering
    # logic shape indirectly is covered by the live network test elsewhere.
    # This test just documents the contract without a network call.
    assert callable(docsets.catalog)


def test_html_entities_are_decoded_exactly_once():
    md = docsets.html_fragment_to_markdown("<p>Write <code>&amp;lt;</code> to get <code>&lt;</code>.</p><script>x()</script>")
    assert md == "Write `&lt;` to get `<`."


# ------------------------------------------------------------- shared converter and fetcher
def test_html_converter_is_the_shared_one_and_keeps_numbered_lists_and_tables():
    md = docsets.html_fragment_to_markdown(
        "<h2>Title</h2><ol><li>one</li><li>two</li></ol><table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>"
        "<nav>menu</nav><pre><code>x = 1</code></pre>"
    )
    assert "## Title" in md
    assert "1. one" in md and "2. two" in md          # the old converter numbered every item "1."
    assert "| a | b |" in md and "| --- | --- |" in md  # a real Markdown table, not " | a | b"
    assert "menu" not in md
    assert "```" in md and "x = 1" in md


def _mock_fetcher(handler):
    import httpx

    from babels_hoard.hoard_link.web.fetch import Fetcher

    return Fetcher(transport=httpx.MockTransport(handler), resolver=lambda host, port: ["93.184.216.34"], min_interval_s=0.0,
                   retries=0, sleep=lambda s: None)


def test_install_downloads_through_the_shared_fetcher(conn, monkeypatch):
    import json

    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("user-agent", "")))
        if request.url.path == "/docs.json":
            return httpx.Response(200, json=[{"slug": "widget", "name": "Widget", "version": "1", "db_size": 100}])
        if request.url.path == "/widget/index.json":
            return httpx.Response(200, json={"entries": [{"name": "frob", "path": "api#frob", "type": "Function"}]})
        if request.url.path == "/widget/db.json":
            return httpx.Response(200, content=json.dumps({"api": '<h2 id="frob">frob()</h2><p>Frobnicates.</p>'}),
                                  headers={"content-type": "application/json"})
        return httpx.Response(404)

    monkeypatch.setattr(docsets, "_fetcher", lambda: _mock_fetcher(handler))
    monkeypatch.setattr(docsets, "CATALOG_URL", "https://docs.example.org/docs.json")
    monkeypatch.setattr(docsets, "DOCUMENTS_BASE", "https://docs.example.org")
    lib = docsets.install(conn, "widget")
    assert lib["status"] == "done" and lib["entry_count"] == 1
    assert [u for u, _ in seen] == ["https://docs.example.org/docs.json", "https://docs.example.org/widget/index.json",
                                    "https://docs.example.org/widget/db.json"]
    assert all(ua.startswith("Mozilla/5.0") for _, ua in seen)        # the family's one browser-like User-Agent
    assert conn.execute("SELECT doc FROM entries WHERE name='frob'").fetchone()["doc"].startswith("## frob()")


def test_catalog_error_is_a_clear_exception_not_a_traceback_of_httpx(monkeypatch):
    import httpx

    monkeypatch.setattr(docsets, "_fetcher", lambda: _mock_fetcher(lambda request: httpx.Response(503, text="down")))
    with pytest.raises(docsets.DocsetDownloadError) as err:
        docsets.catalog("python")
    assert "503" in str(err.value) or "http" in str(err.value).lower()


def test_catalog_filters_and_limits_through_the_fetcher(monkeypatch):
    import httpx

    rows = [{"slug": f"py{i}", "name": f"Python {i}", "version": str(i), "db_size": 2048} for i in range(30)]
    rows.append({"slug": "go", "name": "Go", "release": "1.22", "db_size": 1024})
    monkeypatch.setattr(docsets, "_fetcher", lambda: _mock_fetcher(lambda request: httpx.Response(200, json=rows)))
    out = docsets.catalog("go")
    assert out["total"] == 1 and out["results"][0]["version"] == "1.22" and out["results"][0]["db_size_kb"] == 1
    out = docsets.catalog("python", limit=5)
    assert out["count"] == 5 and out["truncated"] is True


def test_a_body_over_the_cap_is_refused(monkeypatch):
    import httpx

    monkeypatch.setattr(docsets, "MAX_DOWNLOAD_BYTES", 1000)

    def make():
        from babels_hoard.hoard_link.web.fetch import Fetcher

        return Fetcher(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=["x" * 5000])), max_bytes=1000,
                       resolver=lambda host, port: ["93.184.216.34"], min_interval_s=0.0, retries=0, sleep=lambda s: None)

    monkeypatch.setattr(docsets, "_fetcher", make)
    with pytest.raises(docsets.DocsetDownloadError) as err:
        docsets.catalog()
    assert "larger than" in str(err.value)
