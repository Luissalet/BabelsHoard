from pathlib import Path

from babels_hoard import db, indexing

FIXTURES = Path(__file__).parent / "fixtures"


def test_index_real_package_httpx(conn, builtin_env, probe):
    lib = indexing.index_python_library(conn, env=builtin_env, probe=probe, import_name="httpx")
    assert lib["status"] in ("done", "partial")
    assert lib["version"]
    entries = conn.execute("SELECT COUNT(*) c FROM entries WHERE library_id=?", (lib["id"],)).fetchall()
    assert entries[0]["c"] > 20

    row = conn.execute(
        "SELECT * FROM entries WHERE qualname='httpx.Client.get'"
    ).fetchone()
    assert row is not None
    assert row["kind"] == "method"
    params = db.from_json(row["params_json"])
    assert any(p["name"] == "url" for p in params)


def test_index_stdlib_module(conn, builtin_env, probe):
    lib = indexing.index_stdlib_module(conn, env=builtin_env, probe=probe, module_name="json")
    assert lib["status"] == "done"
    row = conn.execute("SELECT * FROM entries WHERE qualname='json.dumps'").fetchone()
    assert row is not None
    assert row["kind"] == "function"


def test_index_fakelib_v1_has_append(conn, builtin_env, probe):
    lib = indexing.index_python_library(
        conn,
        env=builtin_env,
        probe=probe,
        import_name="fakelib",
        dist_name="fakelib",
        version="1.0.0",
        search_paths=[str(FIXTURES / "fakelib_v1")],
    )
    assert lib["status"] == "done"
    row = conn.execute("SELECT * FROM entries WHERE qualname='fakelib.Table.append'").fetchone()
    assert row is not None
    params = db.from_json(row["params_json"])
    assert [p["name"] for p in params if p["name"] != "self"] == ["item", "ignore_index"]


def test_index_fakelib_v2_removed_append_added_concat(conn, builtin_env, probe):
    lib = indexing.index_python_library(
        conn,
        env=builtin_env,
        probe=probe,
        import_name="fakelib",
        dist_name="fakelib",
        version="2.0.0",
        search_paths=[str(FIXTURES / "fakelib_v2")],
    )
    assert lib["status"] == "done"
    assert conn.execute("SELECT * FROM entries WHERE qualname='fakelib.Table.append'").fetchone() is None
    assert conn.execute("SELECT * FROM entries WHERE qualname='fakelib.concat'").fetchone() is not None


def test_unresolvable_alias_recorded_not_skipped(conn, builtin_env, probe):
    """os.path is a conditional cross-module alias griffe cannot statically
    resolve; it must still show up as *existing* to avoid a false positive
    in api_check_code (see test_checker for the end-to-end proof)."""
    indexing.index_stdlib_module(conn, env=builtin_env, probe=probe, module_name="os")
    row = conn.execute("SELECT * FROM entries WHERE qualname='os.path'").fetchone()
    assert row is not None


def test_signatures_keep_keyword_only_and_positional_only_markers(conn, builtin_env, probe):
    indexing.index_python_library(
        conn, env=builtin_env, probe=probe, import_name="edgelib", dist_name="edgelib", version="1",
        search_paths=[str(FIXTURES / "edgelib")],
    )
    init = conn.execute("SELECT signature FROM entries WHERE qualname='edgelib.Session.__init__'").fetchone()
    assert init["signature"].startswith("__init__(self, url: str, *, timeout: float = 5.0)")
    send = conn.execute("SELECT signature FROM entries WHERE qualname='edgelib.Session.send'").fetchone()
    assert "(self, data: bytes, /, retries: int = 0)" in send["signature"]
