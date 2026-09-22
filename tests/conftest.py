import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from babels_hoard import db, environments  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"


@pytest.fixture()
def conn(tmp_path):
    connection = db.connect(tmp_path / "babel.db")
    yield connection
    connection.close()


@pytest.fixture()
def builtin_env(conn):
    env_id = environments.builtin_env_id(conn)
    return environments.get_environment(conn, env_id)


@pytest.fixture()
def probe(builtin_env):
    return environments.probe_python(Path(builtin_env["python_path"]))


@pytest.fixture(scope="session")
def web_stack_template(tmp_path_factory):
    """pydantic + fastapi (+ httpx) indexed once per test session; tests copy
    the file instead of re-indexing ~9,000 entries each time."""
    from babels_hoard import indexing

    path = tmp_path_factory.mktemp("web-stack") / "babel.db"
    connection = db.connect(path)
    env = environments.get_environment(connection, environments.builtin_env_id(connection))
    probe = environments.probe_python(Path(env["python_path"]))
    for name in ("httpx", "pydantic", "fastapi"):
        indexing.index_python_library(connection, env=env, probe=probe, import_name=name)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return path


@pytest.fixture()
def web_conn(tmp_path, web_stack_template):
    import shutil

    target = tmp_path / "babel.db"
    shutil.copyfile(web_stack_template, target)
    connection = db.connect(target)
    yield connection
    connection.close()


@pytest.fixture()
def web_env(web_conn):
    return environments.get_environment(web_conn, environments.builtin_env_id(web_conn))
