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
    result = search.api_lookup(conn, "httpx.Client.gett", env_row=builtin_env)
    assert result["found"] is False
    assert "get" in result["suggestions"]


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
