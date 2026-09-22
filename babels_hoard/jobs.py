"""A tiny background job runner: a worker thread with a queue, so long work
(indexing all project dependencies, installing a docset) doesn't block HTTP
handlers. State lives in the ``jobs`` table so the UI can poll progress.

Job functions receive ``(conn, progress)``: ``conn`` is the worker thread's
own SQLite connection (never a request thread's), ``progress(pct, msg)``
updates the job row.
"""
from __future__ import annotations

import logging
import queue
import threading
import traceback
import uuid
from typing import Any, Callable

from . import db

logger = logging.getLogger("babels_hoard.jobs")

JobFn = Callable[[Any, Callable[[float, str], None]], dict[str, Any]]


class JobManager:
    def __init__(self, database: db.Database):
        self.database = database
        self._q: "queue.Queue[tuple[str, JobFn]]" = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="babel-jobs", daemon=True)
        self._started = False
        self._start_lock = threading.Lock()

    def start(self) -> None:
        with self._start_lock:
            if not self._started:
                self._started = True
                self._thread.start()

    def submit(self, kind: str, fn: JobFn, message: str = "queued") -> str:
        job_id = "job-" + uuid.uuid4().hex[:12]
        conn = self.database.conn()
        conn.execute(
            "INSERT INTO jobs (id, kind, status, progress, message, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (job_id, kind, "queued", 0.0, message[:300], db.now(), db.now()),
        )
        conn.commit()
        self._q.put((job_id, fn))
        self.start()
        return job_id

    def _worker(self) -> None:
        while True:
            job_id, fn = self._q.get()
            conn = self.database.conn()
            conn.execute("UPDATE jobs SET status='running', updated_at=? WHERE id=?", (db.now(), job_id))
            conn.commit()

            def progress(pct: float, message: str, _job_id: str = job_id) -> None:
                c = self.database.conn()
                c.execute(
                    "UPDATE jobs SET progress=?, message=?, updated_at=? WHERE id=?",
                    (max(0.0, min(1.0, pct)), message[:300], db.now(), _job_id),
                )
                c.commit()

            try:
                result = fn(conn, progress)
                conn.execute(
                    "UPDATE jobs SET status='done', progress=1.0, message='done', result_json=?, updated_at=? WHERE id=?",
                    (db.to_json(result), db.now(), job_id),
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("job %s failed: %s", job_id, exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
                tb = traceback.format_exc(limit=5)
                conn.execute(
                    "UPDATE jobs SET status='error', error=?, message=?, updated_at=? WHERE id=?",
                    (f"{exc}\n{tb}"[:2000], str(exc)[:300], db.now(), job_id),
                )
                conn.commit()

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = self.database.conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        d = db.dump(row)
        d["result"] = db.from_json(d.pop("result_json"))
        return d

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.database.conn().execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for row in rows:
            d = db.dump(row)
            d["result"] = db.from_json(d.pop("result_json"))
            out.append(d)
        return out

    def compact(self, limit: int = 5) -> list[dict[str, Any]]:
        """Recent jobs in the shape the agent sees: no tracebacks, no results."""
        rows = self.database.conn().execute(
            "SELECT id, kind, status, progress, message, updated_at FROM jobs "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "kind": r["kind"],
                "status": r["status"],
                "progress": round(r["progress"] or 0.0, 2),
                "message": r["message"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
