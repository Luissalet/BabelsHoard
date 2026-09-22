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


def test_code_fences_duplicates_rst_and_skipped_folders(conn, tmp_path):
    docs = tmp_path / "proj"
    (docs / "node_modules" / "dep").mkdir(parents=True)
    (docs / "node_modules" / "dep" / "README.md").write_text("# Dependency readme\n", encoding="utf-8")
    (docs / "guide.md").write_text(
        "# Install\n\n```bash\n# not a heading\npip install x\n```\n\n## Usage\nfirst\n\n## Usage\nsecond\n",
        encoding="utf-8",
    )
    (docs / "api.rst").write_text("Reference\n=========\n\nSome text.\n\nFunctions\n---------\n\nMore.\n", encoding="utf-8")
    lib = markdown_index.index_folder(conn, str(docs), "Proj")
    names = [r["qualname"] for r in conn.execute("SELECT qualname FROM entries WHERE library_id=? ORDER BY rowid", (lib["id"],))]
    assert "guide.md#Install" in names
    assert "guide.md#Usage" in names and "guide.md#Usage (2)" in names
    assert not any("not a heading" in n for n in names)
    assert "api.rst#Reference" in names and "api.rst#Functions" in names
    assert not any("Dependency" in n for n in names)
    install = conn.execute("SELECT doc FROM entries WHERE qualname='guide.md#Install'").fetchone()["doc"]
    assert "# not a heading" in install
