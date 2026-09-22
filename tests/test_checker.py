from pathlib import Path

from babels_hoard import checker, indexing

FIXTURES = Path(__file__).parent / "fixtures"


def _index_fakelib(conn, env, probe, version_dir, version):
    return indexing.index_python_library(
        conn,
        env=env,
        probe=probe,
        import_name="fakelib",
        dist_name="fakelib",
        version=version,
        search_paths=[str(FIXTURES / version_dir)],
    )


def test_fakelib_v1_append_is_fine(conn, builtin_env, probe):
    _index_fakelib(conn, builtin_env, probe, "fakelib_v1", "1.0.0")
    code = "import fakelib\nt = fakelib.Table()\nt.append({'a': 1})\n"
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    assert result["ok"] is True
    assert result["findings"] == []
    assert result["checked"] > 0


def test_fakelib_v2_append_is_flagged_removed(conn, builtin_env, probe):
    _index_fakelib(conn, builtin_env, probe, "fakelib_v2", "2.0.0")
    code = "import fakelib\nt = fakelib.Table()\nt.append({'a': 1})\n"
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    assert result["ok"] is False
    codes = [f["code"] for f in result["findings"]]
    assert "unknown_attribute" in codes
    assert any(f["symbol"] == "fakelib.Table.append" for f in result["findings"])


def test_fakelib_v2_concat_replacement_is_fine(conn, builtin_env, probe):
    _index_fakelib(conn, builtin_env, probe, "fakelib_v2", "2.0.0")
    code = "import fakelib\nr = fakelib.concat([fakelib.Table()], ignore_index=True)\n"
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    assert result["ok"] is True
    assert result["findings"] == []


def test_real_package_known_good_snippet(conn, builtin_env, probe):
    indexing.index_python_library(conn, env=builtin_env, probe=probe, import_name="httpx")
    code = "import httpx\nc = httpx.Client()\nr = c.get('https://example.com', params={'a': 1})\n"
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    assert result["ok"] is True
    assert "httpx@" in result["libraries"][0] or result["libraries"] == [] or any(
        lib.startswith("httpx@") for lib in result["libraries"]
    )


def test_real_package_known_bad_snippet(conn, builtin_env, probe):
    indexing.index_python_library(conn, env=builtin_env, probe=probe, import_name="httpx")
    code = (
        "import httpx\n"
        "c = httpx.Client()\n"
        "c.get('https://example.com', totally_fake_kw=1)\n"
        "c.nonexistent_method()\n"
    )
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    assert result["ok"] is False
    codes = {f["code"] for f in result["findings"]}
    assert "unexpected_keyword" in codes
    assert "unknown_attribute" in codes


def test_syntax_error_is_reported(conn, builtin_env):
    result = checker.api_check_code(conn, "def f(:\n  pass", env_row=builtin_env)
    assert result["ok"] is False
    assert result["findings"][0]["code"] == "syntax_error"


def test_unresolvable_alias_does_not_produce_false_positive(conn, builtin_env, probe):
    indexing.index_stdlib_module(conn, env=builtin_env, probe=probe, module_name="os")
    code = "import os\nos.path.join('a', 'b')\n"
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    # os.path is an unresolved cross-module alias: Babel must stay silent
    # (unchecked) rather than falsely claim os.path or os.path.join is missing.
    assert result["findings"] == []
    assert result["unchecked"] >= 1


def test_missing_required_argument(conn, builtin_env, probe):
    _index_fakelib(conn, builtin_env, probe, "fakelib_v2", "2.0.0")
    code = "import fakelib\nfakelib.concat()\n"
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    assert result["ok"] is False
    assert any(f["code"] == "missing_required" for f in result["findings"])


def test_unknown_module_import(conn, builtin_env, probe):
    _index_fakelib(conn, builtin_env, probe, "fakelib_v1", "1.0.0")
    code = "import fakelib.notreal\n"
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    assert result["ok"] is False
    assert result["findings"][0]["code"] == "unknown_module"
