"""Regression tests for the checker's zero-false-positive promise and for
the value tracking that gives it coverage.

``edgelib`` (tests/fixtures/edgelib) is read statically, never imported.
The stdlib and httpx cases are the real packages in the test interpreter:
each one below was a false positive (or a missed error) before the
completeness metadata existed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from babels_hoard import checker, indexing

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def edge_env(conn, builtin_env, probe):
    indexing.index_python_library(
        conn,
        env=builtin_env,
        probe=probe,
        import_name="edgelib",
        dist_name="edgelib",
        version="1.0.0",
        search_paths=[str(FIXTURES / "edgelib")],
    )
    return builtin_env


def check(conn, env, code):
    return checker.api_check_code(conn, code, env_row=env)


def codes(result):
    return [(f["line"], f["severity"], f["code"]) for f in result["findings"]]


# --------------------------------------------------------------- stdlib --
@pytest.mark.parametrize(
    "code",
    [
        "import os\nos.getcwd()\nos.listdir('.')\nos.environ.get('X')\n",
        "import socket\nsocket.AF_INET\nsocket.socket(socket.AF_INET, socket.SOCK_STREAM)\n",
        "import re\nre.IGNORECASE\nre.compile('x', re.I)\n",
        "import hashlib\nhashlib.sha256(b'x').hexdigest()\n",
        "import sqlite3\nsqlite3.connect(':memory:')\nsqlite3.Row\n",
        "import math, sys, time\nmath.sqrt(2)\nsys.argv\ntime.sleep(0)\n",
        "import tempfile\nwith tempfile.TemporaryDirectory() as d:\n    d.upper()\n",
        "import os.path\nos.path.join('a', 'b')\nos.getcwd()\n",
        # lazy names listed in __all__ and served by a module __getattr__
        "from concurrent.futures import ThreadPoolExecutor\nimport concurrent.futures\n"
        "with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:\n    ex.submit(print, 1)\n",
        # Python classes shadowed by `from _datetime import *`, attributes set after the class body
        "import datetime as dt\ndt.timezone.utc\ndt.datetime.min\ndt.datetime.now().isoformat()\n",
        # enum _convert_(..., __name__) injects the constants into the module
        "import ssl\nssl.PROTOCOL_TLS_CLIENT\nssl.CERT_NONE\nssl.VerifyMode\n",
    ],
)
def test_stdlib_names_hidden_from_static_analysis_are_not_flagged(conn, builtin_env, code):
    result = check(conn, builtin_env, code)
    assert result["findings"] == [], result["findings"]


def test_stdlib_real_error_still_caught(conn, builtin_env):
    # json.dumps takes **kw, so a misspelt keyword is legal there; a missing
    # required argument is not.
    result = check(conn, builtin_env, "import json\njson.dumps({}, sort_key=True)\njson.dump({})\njson.loadz('x')\n")
    assert [f["code"] for f in result["findings"]] == ["missing_required", "unknown_attribute"]
    assert any(f["code"] == "unknown_attribute" and f.get("suggestion") in ("load", "loads") for f in result["findings"])
    assert result["ok"] is False


# ---------------------------------------------------------------- scopes --
def test_shadowing_and_stores_are_respected(conn, builtin_env):
    code = """
import json
import httpx

c = httpx.Client()
c.custom_flag = True          # creating an attribute is not an error
del c.custom_flag

def handler(json):            # parameter shadows the module
    return json.payload

for httpx in [1]:
    httpx.real

items = [json.anything for json in [1, 2]]

try:
    from json import brand_new_api
except ImportError:
    brand_new_api = None

if hasattr(json, "future_thing"):
    json.future_thing()

ok = hasattr(json, "x") and json.x()
"""
    result = check(conn, builtin_env, code)
    assert result["findings"] == [], result["findings"]


def test_private_and_dunder_names_are_never_flagged(conn, builtin_env):
    code = "import httpx\nhttpx.__version__\nhttpx._client\nc = httpx.Client()\nc._transport\nc.__class__\n"
    assert check(conn, builtin_env, code)["findings"] == []


def test_unbound_method_call_counts_self(conn, builtin_env):
    code = "import httpx\nc = httpx.Client()\nhttpx.Client.get(c, 'https://x')\n"
    assert check(conn, builtin_env, code)["findings"] == []


# ------------------------------------------------------ value inference --
def test_return_annotation_and_context_managers_give_coverage(conn, builtin_env):
    code = """
import httpx

client = httpx.Client()
r = client.get("https://x")
r.jsn()
resp = httpx.get("u")
resp.not_a_thing
httpx.Client(timeout_seconds=3)

with httpx.Client() as c:
    c.gett("x")

async def main():
    async with httpx.AsyncClient() as ac:
        await ac.gett("x")
        page = await ac.get("x")
        page.jsonn()
    pending = ac.get("x")    # not awaited: a coroutine, not a Response
    pending.anything

def typed(c: httpx.Client) -> None:
    c.postt("x")
"""
    result = check(conn, builtin_env, code)
    found = {(f["line"], f["code"], f["symbol"]) for f in result["findings"]}
    assert (6, "unknown_attribute", "httpx.Response.jsn") in found
    assert (8, "unknown_attribute", "httpx.Response.not_a_thing") in found
    assert any(f[0] == 9 and f[1] == "unexpected_keyword" for f in found)
    assert (12, "unknown_attribute", "httpx.Client.gett") in found
    assert (16, "unknown_attribute", "httpx.AsyncClient.gett") in found
    assert (18, "unknown_attribute", "httpx.Response.jsonn") in found
    assert (23, "unknown_attribute", "httpx.Client.postt") in found
    assert not any(f[0] == 20 for f in found)
    suggestions = {f["symbol"]: f.get("suggestion") for f in result["findings"]}
    assert suggestions["httpx.Response.jsn"] == "json"


def test_known_good_httpx_code_is_clean(conn, builtin_env):
    code = """
import httpx
from httpx import Client, Timeout

with httpx.Client(timeout=10, follow_redirects=True) as client:
    r = client.get("https://x", params={"a": 1})
    r.raise_for_status()
    print(r.status_code, r.json(), r.headers, r.text)
    client.post("https://x", json={}, headers={"a": "b"})
t = Timeout(5.0)
Client(base_url="https://x").close()
url = httpx.URL("https://a")
print(url.host, url.scheme)
"""
    result = check(conn, builtin_env, code)
    assert result["findings"] == [], result["findings"]
    assert result["checked"] >= 15


def test_real_pydantic_and_fastapi_imports(web_conn, web_env):
    code = """
from pydantic import BaseModel, Field, ConfigDict
from fastapi import FastAPI, APIRouter, Depends, HTTPException

class Item(BaseModel):
    name: str = Field(default="x", description="d")

app = FastAPI(title="x")
router = APIRouter(prefix="/x")
app.include_router(router)
app.add_middleware
"""
    result = check(web_conn, web_env, code)
    assert result["findings"] == [], result["findings"]
    moved = check(web_conn, web_env, "from pydantic import BaseSettings\n")
    # moved to pydantic-settings in v2; pydantic resolves unknown names through
    # a module __getattr__, so this is reported but only as a warning.
    assert [(f["severity"], f["code"]) for f in moved["findings"]] == [("warning", "unknown_attribute")]
    assert moved["ok"] is True


# ------------------------------------------------------------- edgelib --
def test_names_injected_by_module_level_calls_are_not_flagged(conn, edge_env):
    code = """
from edgelib import injected, registered, quiet
injected.injected
registered.ALPHA
quiet.plain()
quiet.nothing_here
"""
    result = check(conn, edge_env, code)
    assert codes(result) == [(6, "error", "unknown_attribute")], result["findings"]


def test_isinstance_narrowing_is_respected(conn, builtin_env):
    code = """
import httpx

def handle(t: httpx.BaseTransport, c: httpx.Client) -> None:
    if isinstance(t, httpx.HTTPTransport):
        t.anything_on_subclass
    if not isinstance(c, httpx.Client):
        return
    c.anything_after_narrowing
    assert isinstance(t, httpx.MockTransport)
    t.handler
"""
    assert check(conn, builtin_env, code)["findings"] == []


def test_attributes_assigned_after_the_class_body(conn, edge_env):
    code = """
import edgelib
from edgelib import patched
patched.Color.RED
patched.Color.BLUE
patched.Color.GREEN
patched.Flexible.anything
patched.Color.PURPLE
"""
    result = check(conn, edge_env, code)
    assert codes(result) == [(7, "warning", "unknown_attribute"), (8, "error", "unknown_attribute")], result["findings"]


def test_edgelib_certainty_levels(conn, edge_env):
    code = """
import edgelib
from edgelib import lazy, magic, starry, compiled_star

lazy.static_name()
lazy.whatever             # module __getattr__ -> warning only
magic.alpha               # globals() -> silent
starry.from_star()        # star import from a static sibling -> known
starry.nope               # ... so this is certain
compiled_star.anything    # star import from compiled code -> silent
edgelib.Engine().run(3)
edgelib.Engine().walk()   # re-exported from a private module, still checked
"""
    result = check(conn, edge_env, code)
    assert codes(result) == [
        (6, "warning", "unknown_attribute"),
        (9, "error", "unknown_attribute"),
        (12, "error", "unknown_attribute"),
    ], result["findings"]


def test_edgelib_classes(conn, edge_env):
    code = """
import edgelib
d = edgelib.Dynamic()
d.known()
d.unknown_name            # __getattr__ -> warning
s = edgelib.Settable(a=1)
s.a                       # setattr(self, k, v) -> warning at most
f = edgelib.Factory("x")  # __new__: constructor not checked, no instance inferred
f.anything
m = edgelib.Meta(1, 2, 3) # custom metaclass __call__: not checked
c = edgelib.Child()
c.inherited_from_unknown  # unresolved base: silent
edgelib.Decorated().method(1, 2, 3)  # unknown decorator: signature not trusted
edgelib.decorated(1, 2, 3)
edgelib.renamed(old=1)
"""
    result = check(conn, edge_env, code)
    assert codes(result) == [(5, "warning", "unknown_attribute"), (7, "warning", "unknown_attribute")], result["findings"]


def test_edgelib_call_shapes(conn, edge_env):
    code = """
import edgelib
edgelib.Point(1, 2)
edgelib.Point(1, 2, 3)            # too many positional (dataclass __init__)
edgelib.Point(y=2)                # missing x
edgelib.Point(1, z=3)             # unexpected keyword
s = edgelib.Session("u", timeout=2)
s.send(b"x", retries=1)
s.send(data=b"x")                 # positional-only passed by keyword
edgelib.Session.version("x")      # staticmethod through the class
edgelib.Session.from_env("X")     # classmethod through the class
s.open()
s.opened_later                    # assigned outside __init__: exists
edgelib.overloaded(1)
edgelib.overloaded("a", sep=";")
edgelib.overloaded("a", sepp=";") # no overload has sepp
edgelib.connect("h", 1, 2)        # too many positional
cb = edgelib.Callable()
cb(1)
cb(1, 2)                          # __call__ takes one
"""
    result = check(conn, edge_env, code)
    assert codes(result) == [
        (4, "error", "too_many_positional"),
        (5, "error", "missing_required"),
        (6, "error", "unexpected_keyword"),
        (9, "error", "unexpected_keyword"),
        (16, "error", "unexpected_keyword"),
        (17, "error", "too_many_positional"),
        (20, "error", "too_many_positional"),
    ], result["findings"]


def test_edgelib_inference_chains(conn, edge_env):
    code = """
import edgelib
with edgelib.Session("u") as s:
    s.sendd(b"")                     # __enter__ -> Self
with edgelib.Opener() as o:
    o.nothing                        # `return self` without annotation
with edgelib.NotSelf() as n:
    n.upper()                        # __enter__ returns str: no inference
b = edgelib.Builder().step(1).step(2)
b.stepp(3)                           # Self-returning method keeps the type
p = edgelib.Builder().build()
p.z                                  # returns Point
edgelib.connect("h").opn()           # function return annotation
"""
    result = check(conn, edge_env, code)
    assert codes(result) == [
        (4, "error", "unknown_attribute"),
        (6, "error", "unknown_attribute"),
        (10, "error", "unknown_attribute"),
        (12, "error", "unknown_attribute"),
        (13, "error", "unknown_attribute"),
    ], result["findings"]


def test_capped_index_never_produces_errors(conn, builtin_env, probe, monkeypatch):
    """A size-capped library indexes the public surface first (BFS) and
    stays silent about namespaces it did not finish."""
    monkeypatch.setattr(indexing, "MAX_ENTRIES_PER_LIBRARY", 40)
    lib = indexing.index_python_library(conn, env=builtin_env, probe=probe, import_name="httpx", force=True)
    assert lib["status"] == "partial"
    assert conn.execute("SELECT 1 FROM entries WHERE qualname='httpx.Client'").fetchone() is not None
    result = check(conn, builtin_env, "import httpx\nc = httpx.Client()\nc.get('x')\nhttpx.Response\nhttpx.codes\n")
    assert all(f["severity"] != "error" for f in result["findings"]), result["findings"]
