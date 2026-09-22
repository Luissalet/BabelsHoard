"""``python -m babels_hoard`` — start the local server.

Flags: --port, --data-dir, --demo, --no-browser.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__
from .api import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8811


def _setup_logging(data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _seed_demo_data(data_dir: Path) -> None:
    """Register Babel's own interpreter and index a handful of real
    packages plus the fixture docset, so --demo has real signatures without
    touching any of the user's own projects."""
    import sys

    from . import db, docsets, environments, indexing

    conn = db.connect(data_dir / "babel.db")
    already = conn.execute("SELECT COUNT(*) c FROM libraries").fetchone()["c"]
    if already > 0:
        return
    env_id = environments.builtin_env_id(conn)
    env = environments.get_environment(conn, env_id)
    probe = environments.probe_python(Path(env["python_path"]))
    for pkg in ("httpx", "pydantic", "fastapi", "griffe"):
        try:
            indexing.index_python_library(conn, env=env, probe=probe, import_name=pkg)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("babels_hoard").warning("demo indexing of %s failed: %s", pkg, exc)

    fixture_dir = REPO_ROOT / "tests" / "fixtures" / "docset"
    if fixture_dir.is_dir():
        import json

        index_data = json.loads((fixture_dir / "index.json").read_text(encoding="utf-8"))
        db_data = json.loads((fixture_dir / "db.json").read_text(encoding="utf-8"))
        try:
            docsets.install_from_data(conn, "widget-guide", "Widget Guide", "1.0", index_data, db_data)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("babels_hoard").warning("demo docset install failed: %s", exc)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="babels_hoard", description="Babel's Hoard local server")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BABEL_PORT", DEFAULT_PORT)))
    parser.add_argument("--data-dir", type=str, default=None)
    parser.add_argument("--demo", action="store_true", help="seed synthetic demo data in ./data-demo")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab on start")
    args = parser.parse_args(argv)

    if args.data_dir:
        data_dir = Path(args.data_dir)
    elif args.demo:
        data_dir = REPO_ROOT / "data-demo"
    else:
        data_dir = Path(os.environ.get("BABEL_DATA_DIR", REPO_ROOT / "data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    _setup_logging(data_dir)

    if args.demo:
        _seed_demo_data(data_dir)

    static_dir = REPO_ROOT / "frontend" / "dist"
    app = create_app(data_dir=data_dir, static_dir=static_dir if static_dir.is_dir() else None, port=args.port)

    if not args.no_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        except Exception:
            pass

    print(f"Babel's Hoard v{__version__} — http://127.0.0.1:{args.port}  (data: {data_dir})")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
