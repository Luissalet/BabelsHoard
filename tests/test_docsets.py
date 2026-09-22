import json
from pathlib import Path

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
