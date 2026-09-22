from babels_hoard import markdown_index


def test_index_folder_splits_by_heading(conn, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "# Intro\nWelcome.\n\n## Setup\nRun `pip install foo`.\n", encoding="utf-8"
    )
    lib = markdown_index.index_folder(conn, str(docs), name="My Docs")
    assert lib["status"] == "done"
    assert lib["entry_count"] == 2
    row = conn.execute("SELECT * FROM entries WHERE name='Setup'").fetchone()
    assert row is not None
    assert "pip install foo" in row["doc"]


def test_index_folder_rejects_missing_dir(conn, tmp_path):
    import pytest

    with pytest.raises(ValueError):
        markdown_index.index_folder(conn, str(tmp_path / "nope"))
