import json

from fastapi.testclient import TestClient

from babels_hoard.api import create_app
from babels_hoard.locales import check_locales


def test_locale_catalog_reports_key_and_placeholder_drift(tmp_path):
    catalog = tmp_path / "locales"
    catalog.mkdir()
    (catalog / "en.json").write_text(json.dumps({"home": {"title": "Hello {name}", "count": "{n} items"},
                                                   "only_en": "Present"}), encoding="utf-8")
    (catalog / "es.json").write_text(json.dumps({"home": {"title": "Hola {usuario}", "count": "{n} artículos"},
                                                   "only_es": "Sobra"}), encoding="utf-8")
    result = check_locales(catalog)
    assert result["total"] == 3
    assert result["truncated"] is False
    assert {(row["kind"], row["key"]) for row in result["findings"]} == {
        ("missing_key", "only_en"), ("extra_key", "only_es"), ("placeholder_mismatch", "home.title")}
    mismatch = next(row for row in result["findings"] if row["kind"] == "placeholder_mismatch")
    assert mismatch["expected"] == ["name"]
    assert mismatch["actual"] == ["usuario"]
    assert (catalog / "es.json").read_text(encoding="utf-8").endswith('"Sobra"}')


def test_locale_catalog_paginates_and_ignores_complex_messages(tmp_path):
    catalog = tmp_path / "locales"
    catalog.mkdir()
    (catalog / "en.json").write_text(json.dumps({"a": "{n, plural, one {item} other {items}}", "b": "{x}", "c": "{name}"}), encoding="utf-8")
    (catalog / "es.json").write_text(json.dumps({"a": "{n, plural, one {cosa} other {cosas}}", "b": "{y}", "c": "{usuario}"}), encoding="utf-8")
    result = check_locales(catalog, limit=1)
    assert result["total"] == 2
    assert result["has_more"] is True
    assert result["next_offset"] == 1
    assert result["unchecked_messages"] == 1
    assert result["findings"][0]["key"] == "b"
    assert check_locales(catalog, offset=1, limit=1)["findings"][0]["key"] == "c"


def test_locale_catalog_agent_tool_is_read_only_and_audited(tmp_path):
    catalog = tmp_path / "locales"
    catalog.mkdir()
    (catalog / "en.json").write_text('{"hello":"Hi {name}"}', encoding="utf-8")
    (catalog / "es.json").write_text('{"hello":"Hola {nombre}"}', encoding="utf-8")
    before = (catalog / "es.json").read_bytes()
    app = create_app(tmp_path / "app-data", None, port=18817)
    with TestClient(app, base_url="http://127.0.0.1:18817") as client:
        response = client.post("/api/agent/docs_check_locales", json={"path": str(catalog)})
        assert response.status_code == 200
        assert response.json()["findings"][0]["kind"] == "placeholder_mismatch"
        calls = client.get("/api/agent_calls").json()
        assert "docs_check_locales" in str(calls)
    assert (catalog / "es.json").read_bytes() == before


def test_locale_catalog_rejects_invalid_input(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="does not exist"):
        check_locales(tmp_path / "missing")
    catalog = tmp_path / "locales"
    catalog.mkdir()
    (catalog / "en.json").write_text('{"ok":"Hello"}', encoding="utf-8")
    (catalog / "es.json").write_text('{"ok": ["Hola"]}', encoding="utf-8")
    with pytest.raises(ValueError, match="expected a string"):
        check_locales(catalog)
