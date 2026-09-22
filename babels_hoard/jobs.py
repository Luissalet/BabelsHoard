"""A tiny background job runner: a worker thread with a queue, so long work
(indexing all project dependencies, installing a docset) doesn't block HTTP
handlers. State lives in the ``jobs`` table so the UI can poll progress.
"""
from __future__ import annotations

import queue
import threading
import traceback
import uuid
from typing import Any, Callable

from . import db


class JobManager:
    def __init__(self, conn):
        self.conn = conn
        self._q: "queue.Queue[tuple[str, Callable]]" = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def submit(self, kind: str, fn: Callable[[Callable[[float, str], None]], dict[str, Any]]) -> str:
        job_id = "job-" + uuid.uuid4().hex[:12]
        with self._lock:
            self.conn.execute(
                "INSERT INTO jobs (id, kind, status, progress, message, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (job_id, kind, "queued", 0.0, "queued", db.now(), db.now()),
            )
            self.conn.commit()
        self._q.put((job_id, fn))
        self.start()
        return job_id

    def _worker(self) -> None:
        while True:
            job_id, fn = self._q.get()
            with self._lock:
                self.conn.execute(
                    "UPDATE jobs SET status='running', updated_at=? WHERE id=?", (db.now(), job_id)
                )
                self.conn.commit()

            def progress(pct: float, message: str) -> None:
                with self._lock:
                    self.conn.execute(
                        "UPDATE jobs SET progress=?, message=?, updated_at=? WHERE id=?",
                        (pct, message, db.now(), job_id),
                    )
                    self.conn.commit()

            try:
                result = fn(progress)
                with self._lock:
                    self.conn.execute(
                        "UPDATE jobs SET status='done', progress=1.0, result_json=?, updated_at=? WHERE id=?",
                        (db.to_json(result), db.now(), job_id),
                    )
                    self.conn.commit()
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc(limit=5)
                with self._lock:
                    self.conn.execute(
                        "UPDATE jobs SET status='error', error=?, updated_at=? WHERE id=?",
                        (f"{exc}\n{tb}"[:2000], db.now(), job_id),
                    )
                    self.conn.commit()

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        d = db.dump(row)
        d["result"] = db.from_json(d.pop("result_json"))
        return d

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for row in rows:
            d = db.dump(row)
            d["result"] = db.from_json(d.pop("result_json"))
            out.append(d)
        return out
