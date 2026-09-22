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


def test_typescript_named_imports_are_checked_lazily(conn):
    from babels_hoard import checker

    env = {"id": "env-js-test-3", "node_modules_path": str(FIXTURES), "label": "fixture"}
    code = (
        'import { add, type Greeter, sum as total } from "mini-pkg";\n'
        'import Default, { nope } from "mini-pkg";\n'
        'export { Greeter as G, missing } from "mini-pkg";\n'
        'import { whatever } from "./local-file";\n'
        'import { x } from "not-installed";\n'
    )
    result = checker.api_check_code(conn, code, env_row=env, language="typescript")
    assert [(f["line"], f["symbol"]) for f in result["findings"]] == [
        (2, "mini-pkg.nope"),
        (3, "mini-pkg.missing"),
    ]
    assert result["libraries"] == ["mini-pkg@3.1.4"]
    assert result["unchecked"] == 2  # relative import + package that is not installed
    # members of an exported class are not top-level exports
    member = checker.api_check_code(conn, 'import { greet } from "mini-pkg";\n', env_row=env, language="typescript")
    assert member["ok"] is False


def test_js_lookup_through_api_lookup(conn):
    from babels_hoard import search

    env_row = {"id": "env-js-test-4", "node_modules_path": str(FIXTURES), "label": "fixture"}
    conn.execute(
        "INSERT INTO environments (id, label, node_modules_path, is_builtin, created_at) VALUES (?,?,?,0,'x')",
        (env_row["id"], env_row["label"], env_row["node_modules_path"]),
    )
    conn.commit()
    found = search.lookup_with_lazy_index(conn, "mini-pkg.add", env=env_row["id"])
    assert found["found"] is True and "number" in found["signature"]
    missing = search.lookup_with_lazy_index(conn, "mini-pkg.ad", env=env_row["id"])
    assert missing["found"] is False and "mini-pkg.add" in missing["suggestions"]


def test_path_like_package_names_are_refused(conn):
    env = {"id": "env-js-test-5", "node_modules_path": str(FIXTURES)}
    with pytest.raises(node_indexing.NodeIndexError, match="not an npm package name"):
        node_indexing.index_js_library(conn, env=env, package_name="../../etc")
