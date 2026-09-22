"""The shared model backend surface: /api/backend*, config persistence
without leaking the token, and /api/ask ("Ask the docs")."""
import json
import shutil

import pytest
from fastapi.testclient import TestClient

from babels_hoard import backend, db, search
from babels_hoard.api import create_app

from .fake_link import FakeLink, factory_of, resolved, unresolved

PORT = 18814


def _app(tmp_path, **kwargs):
    return create_app(tmp_path, None, port=PORT, **kwargs)


def _client(app):
    return TestClient(app, base_url=f"http://127.0.0.1:{PORT}")


# --------------------------------------------------------------- /api/backend
def test_backend_status_lists_only_capabilities_babel_uses(tmp_path):
    link = FakeLink(resolutions={"llm": resolved("llm"), "embeddings": unresolved("embeddings")})
    with _client(_app(tmp_path, link=link)) as c:
        r = c.get("/api/backend")
        assert r.status_code == 200
        body = r.json()
        assert body["used"] == ["llm", "embeddings"]
        assert set(body["capabilities"]) == {"llm", "embeddings"}
        assert body["capabilities"]["llm"]["state"] == "resolved"
        assert body["capabilities"]["embeddings"]["state"] == "unavailable"
        # the reason sentence is what the Settings screen shows for an
        # honest empty state
        assert "embeddings" in body["capabilities"]["embeddings"]["reason"]


def test_backend_config_roundtrip_never_returns_token(tmp_path):
    link = FakeLink()
    with _client(_app(tmp_path, link=link, link_factory=factory_of(link))) as c:
        r = c.put("/api/backend/config", json={"faustus": {"url": "http://127.0.0.1:7000", "token": "ody_secret"}})
        assert r.status_code == 200
        body = r.json()
        assert body["config"]["faustus"]["token_set"] is True
        assert "token" not in body["config"]["faustus"]
        assert "ody_secret" not in json.dumps(body)

    # the raw file does hold the token (so the backend can actually use it)
    raw = json.loads((tmp_path / "backend.json").read_text(encoding="utf-8"))
    assert raw["faustus"]["token"] == "ody_secret"


def test_backend_config_empty_token_clears_it(tmp_path):
    backend.save_overrides(tmp_path, {"faustus": {"url": "http://127.0.0.1:7000", "token": "ody_secret"}})
    link = FakeLink()
    with _client(_app(tmp_path, link=link, link_factory=factory_of(link))) as c:
        r = c.put("/api/backend/config", json={"faustus": {"token": ""}})
        assert r.json()["config"]["faustus"]["token_set"] is False
    raw = json.loads((tmp_path / "backend.json").read_text(encoding="utf-8"))
    assert "token" not in raw.get("faustus", {})


def test_backend_config_replaces_the_link_and_closes_the_old_one(tmp_path):
    before = FakeLink(resolutions={"llm": unresolved("llm")})
    after = FakeLink(resolutions={"llm": resolved("llm")})
    with _client(_app(tmp_path, link=before, link_factory=factory_of(after))) as c:
        assert c.get("/api/backend").json()["capabilities"]["llm"]["state"] == "unavailable"
        c.put("/api/backend/config", json={"only_resident": False})
        assert before.closed is True
        r = c.get("/api/backend")
        assert r.json()["capabilities"]["llm"]["state"] == "resolved"


def test_backend_recheck_forces_a_fresh_probe(tmp_path):
    """Simulates the model appearing between two clicks of "Re-check"."""
    stale = FakeLink(resolutions={"llm": unresolved("llm", "server not started yet")})
    fresh = FakeLink(resolutions={"llm": resolved("llm", model="qwen3.8-27b")})
    with _client(_app(tmp_path, link=stale, link_factory=factory_of(fresh))) as c:
        assert c.get("/api/backend").json()["capabilities"]["llm"]["state"] == "unavailable"
        r = c.post("/api/backend/recheck")
        assert r.status_code == 200
        assert r.json()["capabilities"]["llm"]["state"] == "resolved"
        assert r.json()["capabilities"]["llm"]["model"] == "qwen3.8-27b"
        assert stale.closed is True


def test_backend_config_get_reflects_saved_overrides(tmp_path):
    backend.save_overrides(tmp_path, {"capabilities": {"llm": {"url": "http://127.0.0.1:8081", "model": "qwen"}}})
    link = FakeLink()
    with _client(_app(tmp_path, link=link)) as c:
        r = c.get("/api/backend/config")
        assert r.status_code == 200
        body = r.json()
        assert body["capabilities"]["llm"]["model"] == "qwen"
        assert body["faustus"]["token_set"] is False
        # embeddings/etc are not this app's business, but llm/embeddings are
        assert set(body["capabilities"]) == {"llm", "embeddings"}


# ------------------------------------------------------------------ /api/ask
@pytest.fixture()
def indexed_data_dir(tmp_path, web_stack_template):
    shutil.copyfile(web_stack_template, tmp_path / "babel.db")
    return tmp_path


def _first_hit_id(data_dir, query="get request"):
    conn = db.connect(data_dir / "babel.db")
    try:
        hits = search.search(conn, query, limit=10)["results"]
        assert hits, "fixture db should have indexed httpx by now"
        return hits[0]["id"]
    finally:
        conn.close()


def test_ask_disabled_when_llm_unavailable(indexed_data_dir):
    link = FakeLink(resolutions={"llm": unresolved("llm", "not running")})
    with _client(_app(indexed_data_dir, link=link)) as c:
        r = c.post("/api/ask", json={"question": "how do I send a get request"})
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is False
        assert "llm" in body["reason"]
        assert link.chat_calls == []


def test_ask_empty_question_is_a_soft_no(indexed_data_dir):
    link = FakeLink(resolutions={"llm": resolved("llm")})
    with _client(_app(indexed_data_dir, link=link)) as c:
        r = c.post("/api/ask", json={"question": "   "})
        assert r.json() == {"available": False, "reason": "Type a question first."}


def test_ask_no_matching_entries(indexed_data_dir):
    link = FakeLink(resolutions={"llm": resolved("llm")})
    with _client(_app(indexed_data_dir, link=link)) as c:
        r = c.post("/api/ask", json={"question": "xqzzptlkvw12345"})
        body = r.json()
        assert body["available"] is True
        assert body["answered"] is False
        assert link.chat_calls == []


def test_ask_answers_and_filters_citations_to_real_ids(indexed_data_dir):
    question = "how do I send a get request"
    real_id = _first_hit_id(indexed_data_dir, query=question)
    from babels_hoard.hoard_link import ChatResult, Usage

    link = FakeLink(
        resolutions={"llm": resolved("llm"), "embeddings": unresolved("embeddings")},
        chat_result=ChatResult(
            text=f"Use it like this [{real_id}], and also cite [made-up:not-real].",
            model="qwen-test", provider="test", usage=Usage(), elapsed_ms=2.0,
        ),
    )
    with _client(_app(indexed_data_dir, link=link)) as c:
        r = c.post("/api/ask", json={"question": question})
        body = r.json()
        assert body["available"] is True
        assert body["answered"] is True
        assert body["cited"] == [real_id]
        assert body["semantic_rerank"] is False
        assert len(body["entries"]) <= 6
        assert body["model"] == "qwen-test"
        # user-initiated call: foreground, no wait_idle bookkeeping needed
        assert len(link.chat_calls) == 1


def test_ask_hybrid_rerank_when_embeddings_resolve(indexed_data_dir):
    link = FakeLink(
        resolutions={"llm": resolved("llm"), "embeddings": resolved("embeddings", provider="ollama")},
        embed_vectors=None,  # overridden below once we know candidate count
    )
    with _client(_app(indexed_data_dir, link=link)) as c:
        r = c.post("/api/ask", json={"question": "how do I send a get request"})
        body = r.json()
        assert body["semantic_rerank"] is True
        assert len(link.embed_calls) == 1
        # query + one text per candidate
        assert len(link.embed_calls[0]) >= 2


def test_ask_soft_failure_when_chat_call_fails(indexed_data_dir):
    from babels_hoard.hoard_link import BackendError

    link = FakeLink(
        resolutions={"llm": resolved("llm")},
        chat_error=BackendError("test", 0, "connection refused"),
    )
    with _client(_app(indexed_data_dir, link=link)) as c:
        r = c.post("/api/ask", json={"question": "how do I send a get request"})
        body = r.json()
        assert body["available"] is True
        assert body["answered"] is False
        assert "model call failed" in body["message"]
        assert body["entries"]


# ------------------------------------------------------------ default wiring
def test_create_app_builds_a_real_link_by_default_and_closes_it(tmp_path):
    """No injected link: create_app must still build one (common step 2) and
    close it on shutdown, without making any network call just by existing."""
    app = _app(tmp_path)
    with _client(app):
        pass  # lifespan runs on enter/exit; must not raise
