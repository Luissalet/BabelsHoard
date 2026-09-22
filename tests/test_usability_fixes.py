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
    code = (
        "import pytest\n"
        "@pytest.fixtur\n"
        "def client():\n"
        "    return 1\n"
        "def test_bad():\n"
        "    with pytest.raises(ValueError, matchh='bad'):\n"
        "        raise ValueError('bad')\n"
        "    with pytest.raises(ValueError, match='bad'):\n"
        "        raise ValueError('bad')\n"
        "    pytest.raises(ValueError, int, 'x', base=10)\n"
    )
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    # raises(E, match=...) cannot be the legacy raises(E, func, *args, **kwargs)
    # overload, so its **kwargs no longer hides the misspelled keyword
    assert [(f["line"], f["code"], f.get("suggestion")) for f in result["findings"]] == [
        (2, "unknown_attribute", "fixture"), (6, "unexpected_keyword", "match"),
    ]


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


# --------------------------------------------------------------------- B2 --
@pytest.fixture()
def capped_edge_env(conn, builtin_env, probe, monkeypatch):
    """edgelib indexed with a 40-entry cap, like SQLAlchemy at 15,000:
    submodules and classes past the cap are recorded but not expanded."""
    with monkeypatch.context() as m:
        m.setattr(indexing, "MAX_ENTRIES_PER_LIBRARY", 40)
        lib = indexing.index_python_library(
            conn, env=builtin_env, probe=probe, import_name="edgelib", dist_name="edgelib", version="1.0.0",
            search_paths=[str(FIXTURES / "edgelib")],
        )
    assert lib["status"] == "partial"
    row = conn.execute("SELECT meta_json FROM entries WHERE qualname='edgelib.stubbed'").fetchone()
    assert '"nx"' in row["meta_json"]
    # on-demand expansion re-reads the package from the interpreter's paths
    monkeypatch.setattr(environments, "probe_python", _probe_with_fixture)
    return builtin_env


_ORIGINAL_PROBE = environments.probe_python


def _probe_with_fixture(path, **kwargs):
    real = _ORIGINAL_PROBE(path, **kwargs)
    return {**real, "sys_path": [str(FIXTURES / "edgelib"), *real["sys_path"]]}


def test_b2_a_namespace_past_the_cap_is_indexed_on_first_lookup(conn, capped_edge_env):
    found = search.lookup_with_lazy_index(conn, "edgelib.stubbed.Generator.normal", env=capped_edge_env["id"])
    assert found["found"], found
    meta = conn.execute("SELECT meta_json FROM entries WHERE qualname='edgelib.stubbed'").fetchone()["meta_json"]
    assert '"nx"' not in (meta or "")


def test_b2_the_checker_expands_on_demand_and_reports_certain_errors(conn, capped_edge_env):
    code = (
        "from edgelib.stubbed import default_rng\n"
        "from edgelib import Session\n"
        "rng = default_rng(0)\n"
        "rng.gaussian(1.0)\n"
        "with Session('sqlite://') as s:\n"
        "    s.url\n"
        "    s.close_all()\n"
    )
    result = checker.api_check_code(conn, code, env_row=capped_edge_env)
    assert _codes(result) == [(4, "error", "unknown_attribute"), (7, "error", "unknown_attribute")], result
    # the expansion was merged into the same library, and is not repeated
    count = conn.execute("SELECT entry_count FROM libraries WHERE name='edgelib'").fetchone()["entry_count"]
    assert count > 40
    again = checker.api_check_code(conn, code, env_row=capped_edge_env)
    assert _codes(again) == _codes(result)
    assert conn.execute("SELECT entry_count FROM libraries WHERE name='edgelib'").fetchone()["entry_count"] == count


# --------------------------------------------------------------------- A9 --
@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("timeout", "httpx.Timeout"),  # was httpx.Client.timeout, then two status-code constants
        ("FastAPI", "fastapi.FastAPI"),  # was the fastapi module, the class not in the top 4
        ("Field", "pydantic.Field"),  # was a helper named is_scalar_field
        ("BaseModel", "pydantic.BaseModel"),
        ("send a get request", "httpx.Client.get"),
    ],
)
def test_a9_search_puts_the_api_you_mean_first(web_conn, query, first):
    result = search.search(web_conn, query, limit=5)
    assert result["results"][0]["qualname"] == first, [h["qualname"] for h in result["results"]]


# --------------------------------------------------------------------- A2 --
def test_a2_async_with_stream_binds_the_response(web_conn, web_env):
    """``client.stream`` is an @asynccontextmanager annotated
    ``-> AsyncIterator[Response]``: the streaming idiom was unverified."""
    code = (
        "import httpx\n"
        "async def relay(url: str):\n"
        "    async with httpx.AsyncClient(timeout=30) as client:\n"
        "        async with client.stream('POST', url, json={}) as response:\n"
        "            response.raise_for_status()\n"
        "            async for line in response.aiter_lines():\n"
        "                yield line\n"
        "            async for line in response.aiter_text_lines():\n"
        "                yield line\n"
        "with httpx.Client() as c:\n"
        "    with c.stream('GET', 'https://x') as r:\n"
        "        r.iter_bytes()\n"
        "        r.iter_byts()\n"
    )
    result = checker.api_check_code(web_conn, code, env_row=web_env)
    assert _codes(result) == [(8, "error", "unknown_attribute"), (13, "error", "unknown_attribute")], result["findings"]
    assert result["findings"][1]["suggestion"] == "iter_bytes"


# --------------------------------------------------------------------- A1 --
def test_a1_the_dependency_job_skips_development_tools(tmp_path):
    from babels_hoard import deps

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["httpx", "SQLAlchemy>=2"]\n'
        '[project.optional-dependencies]\nserver = ["uvicorn"]\ntest = ["hypothesis"]\n'
        '[dependency-groups]\ndev = ["pytest", "ruff", "rich"]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("fastapi\nmypy\npytest-asyncio\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("black\nipython\n", encoding="utf-8")
    runtime, dev = deps.split_dependencies(tmp_path)
    assert runtime == ["fastapi", "httpx", "sqlalchemy", "uvicorn"]
    assert dev == ["black", "hypothesis", "ipython", "mypy", "pytest", "pytest-asyncio", "rich", "ruff"]
    assert deps.direct_dependencies(tmp_path) == runtime
    assert set(deps.direct_dependencies(tmp_path, include_dev=True)) == {*runtime, *dev}


# --------------------------------------------------------------------- A3 --
def test_a3_snippet_classes_inherit_their_bases_members(web_conn, web_env):
    """``item.dict()`` on the user's own pydantic model: the class was not
    followed, and PEP 702 ``@deprecated`` was not read."""
    code = (
        "from pydantic import BaseModel\n"
        "class Item(BaseModel):\n"
        "    name: str\n"
        "    price: float = 0.0\n"
        "    def label(self) -> str:\n"
        "        self.cached = self.name.upper()\n"
        "        return self.cached\n"
        "def show(item: Item) -> dict:\n"
        "    item.label()\n"
        "    item.cached\n"
        "    item.price\n"
        "    item.model_dump(exclude_none=True)\n"
        "    item.model_dump(exclude_nonee=True)\n"
        "    return item.dict()\n"
        "other = Item(name='x')\n"
        "Item.model_validate({'name': 'y'})\n"
    )
    result = checker.api_check_code(web_conn, code, env_row=web_env)
    assert _codes(result) == [(13, "error", "unexpected_keyword"), (14, "warning", "deprecated")], result["findings"]
    assert "model_dump" in result["findings"][1]["message"]


def test_a3_snippet_classes_with_dynamic_hooks_stay_silent(web_conn, web_env):
    code = (
        "from pydantic import BaseModel\n"
        "class Loose(BaseModel):\n"
        "    def __getattr__(self, name):\n"
        "        return 1\n"
        "Loose().anything\n"
        "class Meta(BaseModel, extra='allow'):\n"
        "    pass\n"
        "Meta().whatever\n"
    )
    assert checker.api_check_code(web_conn, code, env_row=web_env)["findings"] == []


# -------------------------------------------------------------------- A10 --
def test_a10_named_imports_from_a_package_with_hundreds_of_exports_are_checked(conn, tmp_path):
    from babels_hoard import node_indexing

    if node_indexing.node_available() is None:
        pytest.skip("Node.js not installed")
    pkg = tmp_path / "node_modules" / "many-icons"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text('{"name": "many-icons", "version": "1.2.3", "types": "index.d.ts"}', encoding="utf-8")
    (pkg / "index.d.ts").write_text(
        "".join(f"export declare const Icon{i}: number;\n" for i in range(700)) + "export declare const Wand: number;\n",
        encoding="utf-8",
    )
    env = {"id": "env-icons", "node_modules_path": str(tmp_path / "node_modules"), "label": "icons"}
    lib = node_indexing.index_js_library(conn, env=env, package_name="many-icons")
    assert lib["status"] == "done", lib["note"]
    code = 'import { Icon3, Icon650, Wand, MagicWand } from "many-icons";\n'
    result = checker.api_check_code(conn, code, env_row=env, language="typescript")
    assert [(f["symbol"], f["severity"]) for f in result["findings"]] == [("many-icons.MagicWand", "error")]
    assert result["checked"] == 4


# -------------------------------------------------------------------- A11 --
def test_a11_compound_keyword_suggests_the_part_that_is_a_parameter(web_conn, web_env):
    result = checker.api_check_code(web_conn, "import httpx\nhttpx.Timeout(10, connect_timeout=5)\n", env_row=web_env)
    assert [(f["code"], f.get("suggestion")) for f in result["findings"]] == [("unexpected_keyword", "connect")]


def test_a11_js_name_exported_by_another_installed_package(conn, tmp_path):
    from babels_hoard import node_indexing

    if node_indexing.node_available() is None:
        pytest.skip("Node.js not installed")
    nm = tmp_path / "node_modules"
    for name, body in (("core-lib", "export declare function useThing(): void;\n"),
                       ("dom-lib", "export declare function useFormThing(): void;\n")):
        (nm / name).mkdir(parents=True)
        (nm / name / "package.json").write_text(f'{{"name": "{name}", "version": "1.0.0", "types": "index.d.ts"}}', encoding="utf-8")
        (nm / name / "index.d.ts").write_text(body, encoding="utf-8")
    env = {"id": "env-two-libs", "node_modules_path": str(nm), "label": "two"}
    node_indexing.index_js_library(conn, env=env, package_name="dom-lib")
    code = 'import { useThing, useFormThing } from "core-lib";\n'
    finding = checker.api_check_code(conn, code, env_row=env, language="typescript")["findings"][0]
    assert "exported by 'dom-lib'" in finding["message"]


def test_a1_no_parsed_package_stays_in_memory_after_indexing(conn, edge_env):
    """edgelib has a @dataclass: griffe's dataclasses extension memoised it
    with functools.cache, keeping every indexed package alive (the 1.5 GB
    after a 50-dependency job)."""
    import gc

    import griffe

    gc.collect()
    alive = [o for o in gc.get_objects() if isinstance(o, griffe.Module) and str(getattr(o, "path", "")).startswith("edgelib")]
    assert alive == []


# ------------------------------------------------ false-positive harness --
def test_attributes_other_code_assigns_on_instances_are_not_errors(conn, builtin_env):
    """Found by scripts/check_corpus.py over _pytest: logging.Formatter.format
    does ``record.message = record.getMessage()``, so LogRecord.message
    exists at runtime although no class body declares it."""
    code = (
        "import logging\n"
        "def show(record: logging.LogRecord) -> str:\n"
        "    record.getMessage()\n"
        "    return record.message + record.asctime + record.levelnme\n"
    )
    result = checker.api_check_code(conn, code, env_row=builtin_env)
    assert [(f["line"], f["symbol"]) for f in result["findings"]] == [(4, "logging.LogRecord.levelnme")]
