"""FastAPI application: HTTP API, browser-attack guard, agent-call audit log,
and static frontend hosting."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from . import __version__, checker, db, deps, docsets, environments, indexing, jobs, markdown_index, node_indexing, search

SERVICE = "babels-hoard"
DISPLAY_NAME = "Babel's Hoard"

_STATE_LOCK = threading.RLock()


class AgentError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _no_index_page(reason: str) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{DISPLAY_NAME}</title></head>
<body style="font-family:system-ui;max-width:640px;margin:80px auto;color:#222">
<h1>{DISPLAY_NAME}</h1>
<p>{reason}</p>
<pre style="background:#f4f4f4;padding:12px;border-radius:8px">cd frontend
npm ci
npm run build</pre>
<p>Then restart the app. The API itself is already running — try
<a href="/api/health">/api/health</a>.</p>
</body></html>"""


class BrowserGuardMiddleware(BaseHTTPMiddleware):
    """DNS-rebinding + cross-site write protection. No CORS is configured, so
    a browser tab on another origin cannot read responses anyway; this stops
    it from causing *writes* via simple requests, and stops any client from
    reaching this app through a non-loopback Host header."""

    def __init__(self, app, port: int):
        super().__init__(app)
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.port = port

    async def dispatch(self, request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in self.allowed_hosts:
            return JSONResponse(
                {"error": "bad_host", "message": f"unexpected Host header: {host!r}"}, status_code=400
            )
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            sec_fetch_site = request.headers.get("sec-fetch-site")
            if sec_fetch_site == "cross-site":
                return JSONResponse(
                    {"error": "cross_site_blocked", "message": "cross-site requests may not modify state"},
                    status_code=403,
                )
            if origin is not None:
                own_origins = {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}
                if origin not in own_origins:
                    return JSONResponse(
                        {"error": "cross_origin_blocked", "message": f"unexpected Origin: {origin!r}"},
                        status_code=403,
                    )
        return await call_next(request)


# ---------------------------------------------------------------- schemas --
class DocsLibrariesArgs(BaseModel):
    ecosystem: str | None = None
    env: str | None = None


class DocsSearchArgs(BaseModel):
    query: str
    library: str | None = None
    ecosystem: str | None = None
    kind: str | None = None
    env: str | None = None
    limit: int = 8


class ApiLookupArgs(BaseModel):
    symbol: str
    env: str | None = None
    library: str | None = None


class ApiCheckCodeArgs(BaseModel):
    code: str
    env: str | None = None
    language: str = "python"


class DocsReadArgs(BaseModel):
    id: str
    offset: int = 0
    max_chars: int = 4000


class DocsAddEnvironmentArgs(BaseModel):
    path: str
    index_dependencies: bool = True


class DocsCatalogArgs(BaseModel):
    query: str = ""
    limit: int = 10


class DocsInstallDocsetArgs(BaseModel):
    slug: str


class DocsIndexFolderArgs(BaseModel):
    path: str
    name: str | None = None


class IndexLibraryArgs(BaseModel):
    env: str
    import_name: str
    ecosystem: str = "python"
    force: bool = False


def create_app(data_dir: Path, static_dir: Path | None, port: int = 8811) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(data_dir / "babel.db")
    job_mgr = jobs.JobManager(conn)
    job_mgr.start()

    with _STATE_LOCK:
        environments.builtin_env_id(conn)

    app = FastAPI(title=DISPLAY_NAME, version=__version__)
    app.add_middleware(BrowserGuardMiddleware, port=port)
    app.state.conn = conn
    app.state.job_mgr = job_mgr
    app.state.static_dir = static_dir

    @app.exception_handler(AgentError)
    async def _agent_error_handler(_request: Request, exc: AgentError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    def log_call(tool: str, args_summary: str, ok: bool, error: str | None, duration_ms: float) -> None:
        with _STATE_LOCK:
            conn.execute(
                "INSERT INTO agent_calls (ts, tool, args_summary, duration_ms, ok, error) VALUES (?,?,?,?,?,?)",
                (db.now(), tool, args_summary[:300], duration_ms, int(ok), error),
            )
            conn.commit()

    def call_tool(name: str, args_summary: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        start = time.monotonic()
        try:
            with _STATE_LOCK:
                result = fn()
            log_call(name, args_summary, True, None, (time.monotonic() - start) * 1000)
            return result
        except AgentError as exc:
            log_call(name, args_summary, False, exc.message, (time.monotonic() - start) * 1000)
            raise
        except Exception as exc:  # noqa: BLE001
            log_call(name, args_summary, False, str(exc), (time.monotonic() - start) * 1000)
            raise AgentError("internal_error", str(exc)[:300], status=500) from exc

    # ------------------------------------------------------------ health --
    @app.get("/api/health")
    def health():
        with _STATE_LOCK:
            n_libs = conn.execute("SELECT COUNT(*) c FROM libraries").fetchone()["c"]
            n_entries = conn.execute("SELECT COUNT(*) c FROM entries").fetchone()["c"]
            n_envs = conn.execute("SELECT COUNT(*) c FROM environments").fetchone()["c"]
        return {
            "service": SERVICE,
            "name": DISPLAY_NAME,
            "version": __version__,
            "status": "ok",
            "libraries": n_libs,
            "entries": n_entries,
            "environments": n_envs,
        }

    # ----------------------------------------------------- agent surface --
    @app.post("/api/agent/docs_libraries")
    def agent_docs_libraries(args: DocsLibrariesArgs):
        return call_tool(
            "docs_libraries",
            f"ecosystem={args.ecosystem} env={args.env}",
            lambda: {
                "libraries": search.list_libraries(conn, ecosystem=args.ecosystem, env=args.env),
                "environments": environments.list_environments(conn),
            },
        )

    @app.post("/api/agent/docs_search")
    def agent_docs_search(args: DocsSearchArgs):
        return call_tool(
            "docs_search",
            args.query,
            lambda: search.search(
                conn, args.query, library=args.library, ecosystem=args.ecosystem, kind=args.kind, env=args.env, limit=args.limit
            ),
        )

    @app.post("/api/agent/api_lookup")
    def agent_api_lookup(args: ApiLookupArgs):
        def run():
            env_row = environments.resolve_env(conn, args.env) if (args.env or not args.library) else None
            result = search.api_lookup(conn, args.symbol, env_row=env_row, library=args.library)
            if not result["found"] and env_row and env_row.get("python_path"):
                top = args.symbol.split(".")[0]
                already = conn.execute(
                    "SELECT 1 FROM libraries WHERE env_id=? AND (name=? OR name=?)",
                    (env_row["id"], top, f"stdlib/{top}"),
                ).fetchone()
                if not already:
                    try:
                        probe = environments.probe_python(Path(env_row["python_path"]))
                        indexing.index_python_import(conn, env=env_row, probe=probe, import_name=top)
                        result = search.api_lookup(conn, args.symbol, env_row=env_row, library=args.library)
                    except Exception:
                        pass
            return result

        return call_tool("api_lookup", args.symbol, run)

    @app.post("/api/agent/api_check_code")
    def agent_api_check_code(args: ApiCheckCodeArgs):
        def run():
            env_row = environments.resolve_env(conn, args.env)
            return checker.api_check_code(conn, args.code, env_row=env_row, language=args.language)

        return call_tool("api_check_code", f"{len(args.code)} chars, {args.language}", run)

    @app.post("/api/agent/docs_read")
    def agent_docs_read(args: DocsReadArgs):
        return call_tool("docs_read", args.id, lambda: search.read_entry(conn, args.id, args.offset, args.max_chars))

    @app.post("/api/agent/docs_add_environment")
    def agent_docs_add_environment(args: DocsAddEnvironmentArgs):
        def run():
            try:
                env_row = environments.register_environment(conn, args.path)
            except environments.ProbeError as exc:
                raise AgentError("registration_failed", str(exc)) from exc
            probe = env_row.pop("probe", None)
            job_id = None
            if args.index_dependencies and probe:
                project_path = Path(env_row["project_path"]) if env_row.get("project_path") else None
                names = deps.direct_dependencies(project_path) if project_path else []
                if names:
                    def work(progress):
                        done = []
                        for i, name in enumerate(names):
                            try:
                                lib = indexing.index_python_library(conn, env=env_row, probe=probe, import_name=name)
                                done.append({"name": name, "status": lib["status"]})
                            except Exception as exc:  # noqa: BLE001
                                done.append({"name": name, "status": "error", "error": str(exc)[:200]})
                            progress((i + 1) / len(names), f"indexed {name}")
                        return {"indexed": done}

                    job_id = job_mgr.submit("index_dependencies", work)
            return {"environment": env_row, "dependency_job_id": job_id}

        return call_tool("docs_add_environment", args.path, run)

    @app.post("/api/agent/docs_catalog")
    def agent_docs_catalog(args: DocsCatalogArgs):
        def run():
            try:
                return docsets.catalog(args.query, args.limit)
            except Exception as exc:  # noqa: BLE001
                raise AgentError("catalog_unavailable", f"could not reach the docset catalogue: {exc}", status=502) from exc

        return call_tool("docs_catalog", args.query, run)

    @app.post("/api/agent/docs_install_docset")
    def agent_docs_install_docset(args: DocsInstallDocsetArgs):
        def run():
            try:
                return docsets.install(conn, args.slug)
            except ValueError as exc:
                raise AgentError("unknown_docset", str(exc)) from exc
            except Exception as exc:  # noqa: BLE001
                raise AgentError("install_failed", f"could not install {args.slug!r}: {exc}", status=502) from exc

        return call_tool("docs_install_docset", args.slug, run)

    @app.post("/api/agent/docs_index_folder")
    def agent_docs_index_folder(args: DocsIndexFolderArgs):
        def run():
            try:
                return markdown_index.index_folder(conn, args.path, args.name)
            except ValueError as exc:
                raise AgentError("bad_path", str(exc)) from exc

        return call_tool("docs_index_folder", args.path, run)

    # --------------------------------------------------------- UI-only ---
    @app.get("/api/environments")
    def ui_environments():
        with _STATE_LOCK:
            return environments.list_environments(conn)

    @app.get("/api/libraries")
    def ui_libraries(ecosystem: str | None = None, env: str | None = None):
        with _STATE_LOCK:
            return search.list_libraries(conn, ecosystem=ecosystem, env=env)

    @app.post("/api/libraries/index")
    def ui_index_library(args: IndexLibraryArgs):
        with _STATE_LOCK:
            env_row = environments.get_environment(conn, args.env)
            if env_row is None:
                raise AgentError("unknown_environment", f"no such environment: {args.env}", status=404)
            if args.ecosystem == "js":
                try:
                    return node_indexing.index_js_library(conn, env=env_row, package_name=args.import_name, force=args.force)
                except node_indexing.NodeIndexError as exc:
                    raise AgentError("index_failed", str(exc), status=502) from exc
            probe = environments.probe_python(Path(env_row["python_path"])) if env_row.get("python_path") else None
            if probe is None:
                raise AgentError("no_python", "this environment has no python interpreter", status=400)
            try:
                return indexing.index_python_import(conn, env=env_row, probe=probe, import_name=args.import_name, force=args.force)
            except indexing.IndexError_ as exc:
                raise AgentError("index_failed", str(exc), status=502) from exc

    @app.post("/api/environments/{env_id}/index-dependencies")
    def ui_index_dependencies(env_id: str):
        with _STATE_LOCK:
            env_row = environments.get_environment(conn, env_id)
            if env_row is None:
                raise AgentError("unknown_environment", f"no such environment: {env_id}", status=404)
            if not env_row.get("python_path"):
                raise AgentError("no_python", "this environment has no python interpreter", status=400)
            probe = environments.probe_python(Path(env_row["python_path"]))
            project_path = Path(env_row["project_path"]) if env_row.get("project_path") else None
            names = deps.direct_dependencies(project_path) if project_path else []

        def work(progress):
            done = []
            for i, name in enumerate(names):
                try:
                    lib = indexing.index_python_library(conn, env=env_row, probe=probe, import_name=name)
                    done.append({"name": name, "status": lib["status"]})
                except Exception as exc:  # noqa: BLE001
                    done.append({"name": name, "status": "error", "error": str(exc)[:200]})
                progress((i + 1) / max(1, len(names)), f"indexed {name}")
            return {"indexed": done}

        job_id = job_mgr.submit("index_dependencies", work)
        return {"job_id": job_id, "dependencies": names}

    @app.get("/api/search")
    def ui_search(q: str, library: str | None = None, ecosystem: str | None = None, kind: str | None = None, env: str | None = None, limit: int = 8):
        with _STATE_LOCK:
            return search.search(conn, q, library=library, ecosystem=ecosystem, kind=kind, env=env, limit=limit)

    @app.get("/api/entries/{entry_id:path}")
    def ui_read_entry(entry_id: str, offset: int = 0, max_chars: int = 4000):
        with _STATE_LOCK:
            return search.read_entry(conn, entry_id, offset, max_chars)

    @app.get("/api/docsets")
    def ui_docsets():
        with _STATE_LOCK:
            return docsets.list_installed(conn)

    @app.get("/api/docsets/catalog")
    def ui_docsets_catalog(q: str = "", limit: int = 20):
        try:
            return docsets.catalog(q, limit)
        except Exception as exc:  # noqa: BLE001
            raise AgentError("catalog_unavailable", f"could not reach the docset catalogue: {exc}", status=502) from exc

    @app.post("/api/docsets/install")
    def ui_docsets_install(args: DocsInstallDocsetArgs):
        def work(progress):
            return docsets.install(conn, args.slug, progress)

        job_id = job_mgr.submit("install_docset", work)
        return {"job_id": job_id}

    @app.post("/api/folders/index")
    def ui_index_folder(args: DocsIndexFolderArgs):
        with _STATE_LOCK:
            try:
                return markdown_index.index_folder(conn, args.path, args.name)
            except ValueError as exc:
                raise AgentError("bad_path", str(exc)) from exc

    @app.post("/api/environments/register")
    def ui_register_environment(args: DocsAddEnvironmentArgs):
        with _STATE_LOCK:
            try:
                env_row = environments.register_environment(conn, args.path)
            except environments.ProbeError as exc:
                raise AgentError("registration_failed", str(exc)) from exc
            env_row.pop("probe", None)
            return env_row

    @app.get("/api/jobs")
    def ui_jobs(limit: int = 20):
        with _STATE_LOCK:
            return job_mgr.list(limit)

    @app.get("/api/jobs/{job_id}")
    def ui_job(job_id: str):
        with _STATE_LOCK:
            job = job_mgr.get(job_id)
            if job is None:
                raise AgentError("not_found", f"no such job: {job_id}", status=404)
            return job

    @app.get("/api/agent_calls")
    def ui_agent_calls(limit: int = 50):
        with _STATE_LOCK:
            rows = conn.execute(
                "SELECT * FROM agent_calls ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return db.dump_all(rows)

    # -------------------------------------------------------- frontend ---
    if static_dir and static_dir.is_dir() and (static_dir / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            candidate = static_dir / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(static_dir / "index.html")

    else:

        @app.get("/")
        def no_ui():
            return HTMLResponse(_no_index_page("The frontend has not been built yet."))

    return app
