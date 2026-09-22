from babels_hoard import indexing, search


def test_search_ranks_relevant_hits_first(conn, builtin_env, probe):
    indexing.index_python_library(conn, env=builtin_env, probe=probe, import_name="httpx")
    result = search.search(conn, "send a get request", ecosystem="python", limit=5)
    assert result["count"] > 0
    top_qualnames = [h["qualname"] for h in result["results"]]
    assert any("get" in q.lower() for q in top_qualnames)


def test_search_filters_by_kind(conn, builtin_env, probe):
    indexing.index_python_library(conn, env=builtin_env, probe=probe, import_name="httpx")
    result = search.search(conn, "Client", kind="class", limit=10)
    assert all(h["kind"] == "class" for h in result["results"])


def test_api_lookup_not_found_gives_suggestions(conn, builtin_env, probe):
    indexing.index_python_library(conn, env=builtin_env, probe=probe, import_name="httpx")
    result = search.lookup_with_lazy_index(conn, "httpx.Client.gett", env=builtin_env["id"])
    assert result["found"] is False
    assert result["certain"] is True
    assert "httpx.Client.get" in result["suggestions"]
    assert "does not exist in httpx 0.28.1" in result["message"]


def test_docs_read_pagination(conn, builtin_env, probe):
    indexing.index_python_library(conn, env=builtin_env, probe=probe, import_name="httpx")
    row = conn.execute(
        "SELECT id FROM entries WHERE qualname='httpx' AND kind='module'"
    ).fetchone()
    first = search.read_entry(conn, row["id"], offset=0, max_chars=50)
    assert first["found"] is True
    assert len(first["text"]) <= 50
    if first["has_more"]:
        second = search.read_entry(conn, row["id"], offset=first["next_offset"], max_chars=50)
        assert second["offset"] == first["next_offset"]


def test_search_collapses_re_exports_of_the_same_object(web_conn):
    result = search.search(web_conn, "BaseModel model_dump", limit=10)
    names = [h["qualname"] for h in result["results"]]
    assert "pydantic.BaseModel.model_dump" in names
    assert not any(n.startswith("fastapi.") and n.endswith("BaseModel.model_dump") for n in names), names


def test_summaries_skip_admonition_markers(web_conn):
    row = web_conn.execute("SELECT summary FROM entries WHERE qualname='pydantic.BaseModel.model_dump'").fetchone()
    assert row["summary"] and not row["summary"].startswith(("!!!", "[")), row["summary"]
    assert "dictionary" in row["summary"].lower()
