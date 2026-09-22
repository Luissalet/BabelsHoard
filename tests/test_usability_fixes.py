"""Regression tests for the findings in docs/USABILITY_REPORT.md (B = blocker,
A = annoying). Each test names the finding it pins down."""
from __future__ import annotations

from babels_hoard import checker, indexing, search


# --------------------------------------------------------------------- B3 --
def test_b3_every_import_name_of_a_distribution_gets_its_own_index(conn, builtin_env, probe):
    """attrs ships ``attr`` and ``attrs`` (like pytest ships ``py`` and
    ``pytest``): the dependency job used to write the first one and skip the
    second as "already indexed"."""
    dist = indexing.distribution_by_name(probe, "attrs")
    assert indexing.import_names_for(probe, dist) == ["attr", "attrs"]
    indexing.index_python_dependency(conn, env=builtin_env, probe=probe, dist_name="attrs")
    rows = conn.execute(
        "SELECT id, status FROM libraries WHERE name='attrs' AND status IN ('done','partial')"
    ).fetchall()
    assert len(rows) == 2
    for qualname in ("attr.s", "attrs.define"):
        found = search.lookup_with_lazy_index(conn, qualname, env=builtin_env["id"])
        assert found["found"], found
    result = checker.api_check_code(
        conn, "import attrs\n@attrs.defin\nclass A:\n    x: int\n", env_row=builtin_env
    )
    # attrs has a module __getattr__, so an unknown name is a warning, not an error
    assert [(f["line"], f["code"]) for f in result["findings"]] == [(2, "unknown_attribute")]


def test_b3_lazy_lookup_indexes_the_second_import_name_too(conn, builtin_env):
    """Lookup order must not matter: asking for ``attrs.define`` first and
    ``attr.ib`` second answers both."""
    assert search.lookup_with_lazy_index(conn, "attrs.define", env=builtin_env["id"])["found"]
    assert search.lookup_with_lazy_index(conn, "attr.ib", env=builtin_env["id"])["found"]


def test_b3_pytest_fixture_is_found_after_the_dependency_job(conn, builtin_env, probe):
    """The live case: the job indexed ``py`` into "pytest 9.1.1" and then
    answered "'pytest' is not installed" for pytest.fixture."""
    indexing.index_python_dependency(conn, env=builtin_env, probe=probe, dist_name="pytest")
    found = search.lookup_with_lazy_index(conn, "pytest.fixture", env=builtin_env["id"])
    assert found["found"] and found["library"].startswith("pytest@"), found


# --------------------------------------------------------------------- B1 --
import os  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from babels_hoard import environments  # noqa: E402
from babels_hoard.api import create_app  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
PORT = 18814


def _fullstack_project(root: Path) -> Path:
    """<root>/.venv/bin/python (the test interpreter) + frontend/node_modules."""
    bin_dir = root / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    os.symlink(sys.executable, bin_dir / "python")
    shutil.copytree(FIXTURES / "jspkg_node_modules", root / "frontend" / "node_modules")
    return root


@pytest.fixture()
def fullstack(tmp_path):
    return _fullstack_project(tmp_path / "proj")


def test_b1_registering_the_root_finds_the_frontend_node_modules(conn, fullstack):
    env = environments.register_environment(conn, str(fullstack))
    assert env["python_path"].endswith("python")
    assert env["node_modules_path"] == str(fullstack / "frontend" / "node_modules")


def test_b1_a_frontend_only_environment_never_becomes_the_python_default(conn, builtin_env, fullstack):
    root = environments.register_environment(conn, str(fullstack))
    front = environments.register_environment(conn, str(fullstack / "frontend"))
    assert front["python_path"] is None
    assert environments.resolve_env(conn, None, "python")["id"] == root["id"]
    # the newest node_modules environment answers TypeScript
    assert environments.resolve_env(conn, None, "typescript")["id"] == front["id"]
    with pytest.raises(environments.NoPython) as exc:
        environments.require_python(conn, front)
    assert root["id"] in str(exc.value)


def test_b1_registering_again_makes_it_the_default_and_keeps_its_id(conn, builtin_env, fullstack, tmp_path):
    other = _fullstack_project(tmp_path / "other")
    first = environments.register_environment(conn, str(fullstack))
    environments.register_environment(conn, str(other))
    assert environments.resolve_env(conn, None)["project_path"] == str(other)
    again = environments.register_environment(conn, str(fullstack))
    assert again["id"] == first["id"]
    assert environments.resolve_env(conn, None)["id"] == first["id"]
    assert environments.resolve_env(conn, None, "python")["id"] == first["id"]


def test_b1_same_interpreter_keeps_its_id_when_node_modules_appears(conn, tmp_path):
    proj = tmp_path / "late"
    (proj / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, proj / ".venv" / "bin" / "python")
    before = environments.register_environment(conn, str(proj))
    shutil.copytree(FIXTURES / "jspkg_node_modules", proj / "web" / "node_modules")
    after = environments.register_environment(conn, str(proj))
    assert after["id"] == before["id"] and after["node_modules_path"].endswith("node_modules")
    assert conn.execute("SELECT COUNT(*) c FROM environments WHERE project_path=?", (str(proj),)).fetchone()["c"] == 1


@pytest.fixture()
def client(tmp_path):
    app = create_app(tmp_path / "data", None, port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def test_b1_python_check_against_a_node_only_env_is_an_error_not_ok(client, tmp_path):
    proj = _fullstack_project(tmp_path / "proj")
    root = client.post("/api/agent/docs_add_environment", json={"path": str(proj), "index_dependencies": False}).json()
    front = client.post(
        "/api/agent/docs_add_environment", json={"path": str(proj / "frontend"), "index_dependencies": False}
    ).json()
    code = "import httpx\nhttpx.Client(retries=3)\n"
    r = client.post("/api/agent/api_check_code", json={"code": code, "env": front["environment"]["id"]})
    assert r.status_code == 400 and r.json()["error"] == "no_python"
    assert root["environment"]["id"] in r.json()["message"]
    # without env the Python default (the root, not the newer frontend) answers
    body = client.post("/api/agent/api_check_code", json={"code": code}).json()
    assert body["env"] == root["environment"]["id"]
    assert [f["code"] for f in body["findings"]] == ["unexpected_keyword"]
    # the TypeScript default is the newest node_modules environment
    ts = client.post(
        "/api/agent/api_check_code",
        json={"code": 'import { miniThing } from "mini-pkg";\n', "language": "typescript"},
    ).json()
    assert ts["env"] == front["environment"]["id"]
    envs = {e["id"]: e for e in client.post("/api/agent/docs_libraries", json={}).json()["environments"]}
    assert envs[root["environment"]["id"]]["default_for"] == ["python"]
    assert envs[front["environment"]["id"]]["default_for"] == ["typescript"]
    # the Activity log names the environment that answered
    calls = client.get("/api/agent_calls").json()
    assert any(str(proj) in c["args_summary"] for c in calls if c["tool"] == "api_check_code")


def test_a7_unknown_environment_says_what_to_do(client, tmp_path):
    r = client.post("/api/agent/api_lookup", json={"symbol": "httpx.Client", "env": str(tmp_path / "nope")})
    assert r.status_code == 404
    assert "docs_add_environment" in r.json()["message"]


# ----------------------------------------------------------------- B5, B6 --
@pytest.fixture()
def edge_env(conn, builtin_env, probe):
    indexing.index_python_library(
        conn, env=builtin_env, probe=probe, import_name="edgelib", dist_name="edgelib", version="1.0.0",
        search_paths=[str(FIXTURES / "edgelib")],
    )
    return builtin_env


def _codes(result):
    return [(f["line"], f["severity"], f["code"]) for f in result["findings"]]


@pytest.mark.parametrize(
    "code",
    [
        # compiled extension without stubs inside a pure-Python package (lxml.etree)
        "from edgelib import fastpart\nfastpart.anything()\n",
        "import edgelib.fastpart as fp\nfp.parse('x')\n",
        # namespace sub-package: a folder without __init__.py (chromadb.api.models)
        "from edgelib.nsparts.tool import helper\nhelper()\n",
        "import edgelib.nsparts\n",
        # methods declared only with @overload in a stub (numpy Generator.normal)
        "from edgelib.stubbed import default_rng\nrng = default_rng(0)\nrng.normal(size=3)\nrng.normal(0.0, 2.0)\n",
        "from edgelib import stubbed\nstubbed.pick([1, 2])\nstubbed.pick(['a'], weights=[1.0])\n",
    ],
)
def test_b5_b6_correct_code_has_no_findings(conn, edge_env, code):
    result = checker.api_check_code(conn, code, env_row=edge_env)
    assert result["findings"] == [], result["findings"]


def test_b6_overload_only_names_are_still_checked(conn, edge_env):
    code = (
        "from edgelib.stubbed import default_rng, pick\n"
        "rng = default_rng(0)\n"
        "rng.normal(sizes=3)\n"
        "rng.gaussian(1.0)\n"
        "pick([1], weight=[1.0])\n"
    )
    result = checker.api_check_code(conn, code, env_row=edge_env)
    assert _codes(result) == [
        (3, "error", "unexpected_keyword"),
        (4, "error", "unknown_attribute"),
        (5, "error", "unexpected_keyword"),
    ]
    found = search.lookup_with_lazy_index(conn, "edgelib.stubbed.Generator.normal", env=edge_env["id"])
    assert found["found"] and found["kind"] == "method"


def test_b5_a_really_missing_submodule_is_still_an_error(conn, edge_env):
    result = checker.api_check_code(conn, "from edgelib import fastpartt\n", env_row=edge_env)
    assert _codes(result) == [(1, "error", "unknown_attribute")]
    assert result["findings"][0]["suggestion"] == "fastpart"
