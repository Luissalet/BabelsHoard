from pathlib import Path

import pytest

from babels_hoard import node_indexing

FIXTURES = Path(__file__).parent / "fixtures" / "jspkg_node_modules"

pytestmark = pytest.mark.skipif(node_indexing.node_available() is None, reason="Node.js not installed")


def test_index_js_library_from_fixture(conn):
    env = {"id": "env-js-test", "node_modules_path": str(FIXTURES)}
    lib = node_indexing.index_js_library(conn, env=env, package_name="mini-pkg")
    assert lib["status"] == "done"
    assert lib["version"] == "3.1.4"

    add_row = conn.execute("SELECT * FROM entries WHERE qualname='mini-pkg.add'").fetchone()
    assert add_row is not None
    assert "number" in add_row["signature"]

    sum_row = conn.execute("SELECT * FROM entries WHERE qualname='mini-pkg.sum'").fetchone()
    assert sum_row["deprecated"] == 1

    member_row = conn.execute("SELECT * FROM entries WHERE qualname='mini-pkg.Greeter.greet'").fetchone()
    assert member_row is not None


def test_unknown_package_raises(conn):
    env = {"id": "env-js-test-2", "node_modules_path": str(FIXTURES)}
    with pytest.raises(node_indexing.NodeIndexError):
        node_indexing.index_js_library(conn, env=env, package_name="does-not-exist")
