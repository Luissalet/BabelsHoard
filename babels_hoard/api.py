"""FastAPI application: HTTP API, browser-attack guard, agent-call audit log,
and static frontend hosting."""
from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.staticfiles import StaticFiles

from . import __version__, checker, db, deps, docsets, environments, indexing, jobs, markdown_index, node_indexing, search

SERVICE = "babels-hoard"
DISPLAY_NAME = "Babel's Hoard"



class AgentError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _confined_file(root: Path, rel: str) -> Path | None:
    """Return ``root/rel`` only if it is an existing file *inside* ``root``.

    ``rel`` comes straight from the URL (already percent-decoded), so it can
    hold ``..`` segments, a leading slash or a Windows drive letter; any of
    those that would leave ``root`` yields ``None`` (the SPA shell is served
    instead)."""
    if not rel or "\x00" in rel:
        return None
    try:
        candidate = (root / rel).resolve()
    except (OSError, ValueError):
        return None
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


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
    limit: int = 30
    offset: int = 0


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
    database = db.Database(data_dir / "babel.db")
    job_mgr = jobs.JobManager(database)
    job_mgr.start()
    environments.builtin_env_id(database.conn())

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        database.close_all()

    app = FastAPI(title=DISPLAY_NAME, version=__version__, lifespan=lifespan)
    app.add_middleware(BrowserGuardMiddleware, port=port)
    app.state.database = database
    app.state.job_mgr = job_mgr
    app.state.static_dir = static_dir

    def C():
        """This thread's own connection (request threads and the job worker
        never share one)."""
        return database.conn()

    @app.exception_handler(AgentError)
    async def _agent_error_handler(_request: Request, exc: AgentError):
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request: Request, exc: RequestValidationError):
        problems = []
        for err in exc.errors()[:5]:
            loc = ".".join(str(part) for part in err.get("loc", ()) if part != "body")
            problems.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
        return JSONResponse(
            {"error": "invalid_arguments", "message": "Invalid arguments - " + "; ".join(problems)},
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(_request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse({"error": code, "message": str(exc.detail)}, status_code=exc.status_code)

    def log_call(tool: str, args_summary: str, ok: bool, error: str | None, duration_ms: float) -> None:
        conn = C()
        conn.execute(
            "INSERT INTO agent_calls (ts, tool, args_summary, duration_ms, ok, error) VALUES (?,?,?,?,?,?)",
            (db.now(), tool, args_summary[:300], round(duration_ms, 1), int(ok), (error or None) and error[:500]),
        )
        conn.commit()

    def call_tool(name: str, args_summary: str, fn: Callable[[Any], dict[str, Any]]) -> dict[str, Any]:
        start = time.monotonic()
        conn = C()
        try:
            result = fn(conn)
            log_call(name, args_summary, True, None, (time.monotonic() - start) * 1000)
            return result
        except AgentError as exc:
            conn.rollback()
            log_call(name, args_summary, False, exc.message, (time.monotonic() - start) * 1000)
            raise
        except environments.ProbeError as exc:
            conn.rollback()
            log_call(name, args_summary, False, str(exc), (time.monotonic() - start) * 1000)
            raise AgentError("unknown_environment", str(exc), status=404) from exc
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            log_call(name, args_summary, False, str(exc), (time.monotonic() - start) * 1000)
            raise AgentError("internal_error", str(exc)[:300], status=500) from exc

    def dependency_job(env_row: dict[str, Any], names: list[str]) -> str:
        def work(conn, progress):
            probe = environments.probe_python(Path(env_row["python_path"]))
            done = []
            for i, name in enumerate(names):
                try:
                    lib = indexing.index_python_dependency(conn, env=env_row, probe=probe, dist_name=name)
                    done.append({"name": name, "status": lib["status"] if lib else "not_installed"})
                except Exception as exc:  # noqa: BLE001
                    done.append({"name": name, "status": "error", "error": str(exc)[:200]})
                progress((i + 1) / max(1, len(names)), f"indexed {name} ({i + 1}/{len(names)})")
            return {"indexed": done}

        return job_mgr.submit("index_dependencies", work, f"{len(names)} dependencies of {env_row['label']}")

    def docset_job(slug: str) -> str:
        def work(conn, progress):
            return docsets.install(conn, slug, progress)

        return job_mgr.submit("install_docset", work, f"docset {slug}")

    # ------------------------------------------------------------ health --
    @app.get("/api/health")
    def health():
        conn = C()
        n_libs = conn.execute("SELECT COUNT(*) c FROM libraries").fetchone()["c"]
        n_entries = conn.execute("SELECT COUNT(*) c FROM entries").fetchone()["c"]
        n_envs = conn.execute("SELECT COUNT(*) c FROM environments").fetchone()["c"]
        running = conn.execute("SELECT COUNT(*) c FROM jobs WHERE status IN ('queued','running')").fetchone()["c"]
        return {
            "service": SERVICE,
            "name": DISPLAY_NAME,
            "version": __version__,
            "status": "ok",
            "libraries": n_libs,
            "entries": n_entries,
            "environments": n_envs,
            "jobs_running": running,
        }

    # ----------------------------------------------------- agent surface --
    @app.post("/api/agent/docs_libraries")
    def agent_docs_libraries(args: DocsLibrariesArgs):
        def run(conn):
            if args.env:
                env_row = environments.resolve_env(conn, args.env)
                env_filter = env_row["id"]
            else:
                env_filter = None
            out = search.libraries_overview(
                conn, ecosystem=args.ecosystem, env=env_filter, limit=args.limit, offset=args.offset
            )
            out["environments"] = environments.compact_environments(conn)
            out["jobs"] = job_mgr.compact(5)
            return out

        return call_tool("docs_libraries", f"ecosystem={args.ecosystem} env={args.env}", run)

    @app.post("/api/agent/docs_search")
    def agent_docs_search(args: DocsSearchArgs):
        def run(conn):
            env_filter = environments.resolve_env(conn, args.env)["id"] if args.env else None
            return search.search(
                conn, args.query, library=args.library, ecosystem=args.ecosystem, kind=args.kind, env=env_filter, limit=args.limit
            )

        return call_tool("docs_search", args.query, run)

    @app.post("/api/agent/api_lookup")
    def agent_api_lookup(args: ApiLookupArgs):
        def run(conn):
            return search.lookup_with_lazy_index(conn, args.symbol, env=args.env, library=args.library)

        return call_tool("api_lookup", args.symbol, run)

    @app.post("/api/agent/api_check_code")
    def agent_api_check_code(args: ApiCheckCodeArgs):
        def run(conn):
            if args.language not in ("python", "typescript"):
                raise AgentError("unsupported_language", "language must be 'python' or 'typescript'")
            env_row = environments.resolve_env(conn, args.env)
            return checker.api_check_code(conn, args.code, env_row=env_row, language=args.language)

        return call_tool("api_check_code", f"{len(args.code)} chars, {args.language}", run)

    @app.post("/api/agent/docs_read")
    def agent_docs_read(args: DocsReadArgs):
        return call_tool("docs_read", args.id, lambda conn: search.read_entry(conn, args.id, args.offset, args.max_chars))

    @app.post("/api/agent/docs_add_environment")
    def agent_docs_add_environment(args: DocsAddEnvironmentArgs):
        def run(conn):
            try:
                env_row = environments.register_environment(conn, args.path)
            except environments.ProbeError as exc:
                raise AgentError("registration_failed", str(exc)) from exc
            env_row.pop("probe", None)
            job_id = None
            names: list[str] = []
            if args.index_dependencies and env_row.get("python_path") and env_row.get("project_path"):
                names = deps.direct_dependencies(Path(env_row["project_path"]))
                if names:
                    job_id = dependency_job(env_row, names)
            return {
                "environment": environments.compact_env(env_row),
                "dependency_job_id": job_id,
                "dependencies": names[:30],
                "message": (
                    f"Registered. Indexing {len(names)} direct dependencies in the background; "
                    "api_lookup/api_check_code also index packages on first use."
                    if job_id
                    else "Registered. Packages are indexed on first use by api_lookup/api_check_code."
                ),
            }

        return call_tool("docs_add_environment", args.path, run)

    @app.post("/api/agent/docs_catalog")
    def agent_docs_catalog(args: DocsCatalogArgs):
        def run(conn):
            try:
                return docsets.catalog(args.query, args.limit)
            except Exception as exc:  # noqa: BLE001
                raise AgentError("catalog_unavailable", f"could not reach the docset catalogue: {exc}", status=502) from exc

        return call_tool("docs_catalog", args.query, run)

    @app.post("/api/agent/docs_install_docset")
    def agent_docs_install_docset(args: DocsInstallDocsetArgs):
        def run(conn):
            slug = args.slug.strip()
            if not docsets.valid_slug(slug):
                raise AgentError("unknown_docset", f"{args.slug!r} is not a docset slug; get one from docs_catalog")
            job_id = docset_job(slug)
            return {
                "job_id": job_id,
                "status": "queued",
                "slug": slug,
                "message": "Downloading and indexing in the background (can take a minute for large docsets). "
                "Call docs_libraries to see the job's progress; the docset is searchable when it shows status done.",
            }

        return call_tool("docs_install_docset", args.slug, run)

    @app.post("/api/agent/docs_index_folder")
    def agent_docs_index_folder(args: DocsIndexFolderArgs):
        def run(conn):
            try:
                return markdown_index.index_folder(conn, args.path, args.name)
            except ValueError as exc:
                raise AgentError("bad_path", str(exc)) from exc

        return call_tool("docs_index_folder", args.path, run)

    # --------------------------------------------------------- UI-only ---
    @app.get("/api/environments")
    def ui_environments():
        return environments.list_environments(C())

    @app.get("/api/environments/{env_id}/packages")
    def ui_env_packages(env_id: str, q: str = "", limit: int = 200):
        conn = C()
        env_row = environments.get_environment(conn, env_id)
        if env_row is None:
            raise AgentError("unknown_environment", f"no such environment: {env_id}", status=404)
        return environments.installed_packages(conn, env_row, query=q, limit=limit)

    @app.get("/api/libraries")
    def ui_libraries(ecosystem: str | None = None, env: str | None = None):
        return search.list_libraries(C(), ecosystem=ecosystem, env=env)

    @app.post("/api/libraries/index")
    def ui_index_library(args: IndexLibraryArgs):
        conn = C()
        env_row = environments.get_environment(conn, args.env)
        if env_row is None:
            raise AgentError("unknown_environment", f"no such environment: {args.env}", status=404)
        if args.ecosystem == "js":
            if not env_row.get("node_modules_path"):
                raise AgentError("no_node_modules", "this environment has no node_modules folder", status=400)
            try:
                return node_indexing.index_js_library(conn, env=env_row, package_name=args.import_name, force=args.force)
            except node_indexing.NodeIndexError as exc:
                raise AgentError("index_failed", str(exc), status=502) from exc
        if not env_row.get("python_path"):
            raise AgentError("no_python", "this environment has no python interpreter", status=400)
        probe = environments.probe_python(Path(env_row["python_path"]))
        try:
            return indexing.index_python_import(conn, env=env_row, probe=probe, import_name=args.import_name, force=args.force)
        except indexing.IndexError_ as exc:
            raise AgentError("index_failed", str(exc), status=502) from exc

    @app.post("/api/environments/{env_id}/index-dependencies")
    def ui_index_dependencies(env_id: str):
        conn = C()
        env_row = environments.get_environment(conn, env_id)
        if env_row is None:
            raise AgentError("unknown_environment", f"no such environment: {env_id}", status=404)
        if not env_row.get("python_path"):
            raise AgentError("no_python", "this environment has no python interpreter", status=400)
        project_path = Path(env_row["project_path"]) if env_row.get("project_path") else None
        names = deps.direct_dependencies(project_path) if project_path else []
        if not names:
            raise AgentError(
                "no_dependencies",
                "no pyproject.toml or requirements*.txt with dependencies next to this environment",
                status=400,
            )
        return {"job_id": dependency_job(env_row, names), "dependencies": names}

    @app.get("/api/search")
    def ui_search(q: str, library: str | None = None, ecosystem: str | None = None, kind: str | None = None, env: str | None = None, limit: int = 8):
        return search.search(C(), q, library=library, ecosystem=ecosystem, kind=kind, env=env, limit=limit)

    @app.get("/api/lookup")
    def ui_lookup(symbol: str, env: str | None = None, library: str | None = None):
        return search.lookup_with_lazy_index(C(), symbol, env=env, library=library, doc_chars=20000)

    @app.get("/api/entries/{entry_id:path}")
    def ui_read_entry(entry_id: str, offset: int = 0, max_chars: int = 4000):
        return search.read_entry(C(), entry_id, offset, max_chars)

    @app.get("/api/docsets")
    def ui_docsets():
        return docsets.list_installed(C())

    @app.get("/api/docsets/catalog")
    def ui_docsets_catalog(q: str = "", limit: int = 20):
        try:
            return docsets.catalog(q, limit)
        except Exception as exc:  # noqa: BLE001
            raise AgentError("catalog_unavailable", f"could not reach the docset catalogue: {exc}", status=502) from exc

    @app.post("/api/docsets/install")
    def ui_docsets_install(args: DocsInstallDocsetArgs):
        if not docsets.valid_slug(args.slug.strip()):
            raise AgentError("unknown_docset", f"{args.slug!r} is not a docset slug")
        return {"job_id": docset_job(args.slug.strip())}

    @app.post("/api/folders/index")
    def ui_index_folder(args: DocsIndexFolderArgs):
        try:
            return markdown_index.index_folder(C(), args.path, args.name)
        except ValueError as exc:
            raise AgentError("bad_path", str(exc)) from exc

    @app.post("/api/environments/register")
    def ui_register_environment(args: DocsAddEnvironmentArgs):
        try:
            env_row = environments.register_environment(C(), args.path)
        except environments.ProbeError as exc:
            raise AgentError("registration_failed", str(exc)) from exc
        env_row.pop("probe", None)
        return env_row

    @app.get("/api/jobs")
    def ui_jobs(limit: int = 20):
        return job_mgr.list(max(1, min(limit, 100)))

    @app.get("/api/jobs/{job_id}")
    def ui_job(job_id: str):
        job = job_mgr.get(job_id)
        if job is None:
            raise AgentError("not_found", f"no such job: {job_id}", status=404)
        return job

    @app.get("/api/agent_calls")
    def ui_agent_calls(limit: int = 50):
        rows = C().execute(
            "SELECT * FROM agent_calls ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)
        ).fetchall()
        return db.dump_all(rows)

    # -------------------------------------------------------- frontend ---
    if static_dir and static_dir.is_dir() and (static_dir / "index.html").is_file():
        static_root = static_dir.resolve()
        if (static_root / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=str(static_root / "assets")), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            if full_path.startswith("api/") or full_path == "api":
                raise AgentError("not_found", f"no such API route: /{full_path}", status=404)
            candidate = _confined_file(static_root, full_path)
            if candidate is not None:
                return FileResponse(candidate)
            return FileResponse(static_root / "index.html")

    else:

        @app.get("/")
        def no_ui():
            return HTMLResponse(_no_index_page("The frontend has not been built yet."))

    return app
