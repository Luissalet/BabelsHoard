"""The HTTP surface must stay responsive while long work runs, and work
running on different threads must not share one SQLite connection."""
from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from babels_hoard import api as api_module
from babels_hoard import db, docsets, markdown_index

PORT = 18815


@pytest.fixture()
def client(tmp_path):
    app = api_module.create_app(tmp_path, None, port=PORT)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as c:
        yield c


def test_health_answers_while_a_slow_tool_runs(client, monkeypatch, tmp_path):
    started = threading.Event()

    def slow_index_folder(conn, path, name=None):
        started.set()
        time.sleep(2.5)
        return {"id": "lib-x", "name": "x", "status": "done", "entry_count": 0}

    monkeypatch.setattr(markdown_index, "index_folder", slow_index_folder)
    worker = threading.Thread(
        target=lambda: client.post("/api/agent/docs_index_folder", json={"path": str(tmp_path)})
    )
    worker.start()
    assert started.wait(5)
    t0 = time.monotonic()
    r = client.get("/api/health")
    elapsed = time.monotonic() - t0
    worker.join()
    assert r.status_code == 200
    assert elapsed < 1.0, f"/api/health blocked for {elapsed:.2f}s behind a tool call"


def test_each_thread_gets_its_own_connection(tmp_path):
    database = db.Database(tmp_path / "babel.db")
    seen = {}

    def grab(name):
        seen[name] = id(database.conn())

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(seen.values())) == 3
    assert database.conn() is database.conn()
    database.close_all()


def test_agent_docset_install_is_a_background_job(client, monkeypatch):
    calls = []

    def fake_install(conn, slug, progress=None):
        calls.append(slug)
        if progress:
            progress(0.5, "halfway")
        return {"id": "lib-doc", "name": slug, "status": "done", "entry_count": 1}

    monkeypatch.setattr(docsets, "install", fake_install)
    r = client.post("/api/agent/docs_install_docset", json={"slug": "python~3.12"})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"].startswith("job-")
    assert body["status"] in ("queued", "running", "done")
    for _ in range(50):
        job = client.get(f"/api/jobs/{body['job_id']}").json()
        if job["status"] == "done":
            break
        time.sleep(0.05)
    assert job["status"] == "done"
    assert calls == ["python~3.12"]
    # the model can follow progress through docs_libraries
    libs = client.post("/api/agent/docs_libraries", json={}).json()
    assert any(j["id"] == body["job_id"] for j in libs["jobs"])
